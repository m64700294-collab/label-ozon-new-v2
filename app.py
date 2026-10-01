import streamlit as st
import re
import os
from io import BytesIO

from pypdf import PdfReader, PdfWriter
import pdfplumber

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import requests


# ============================================================
# НАСТРОЙКИ
# ============================================================

st.set_page_config(
    page_title="Умная склейка этикеток Ozon",
    page_icon="🖨️",
    layout="wide"
)

st.title("🖨️ Склейка: Этикетки + Лист подбора")

st.write(
    "Жёсткое сопоставление: номер этикетки → артикул → количество."
)


# ============================================================
# ШРИФТ
# ============================================================

@st.cache_resource
def load_font():

    font_path = "Roboto_Full_Final.ttf"

    if not os.path.exists(font_path):

        url = (
            "https://cdnjs.cloudflare.com/ajax/libs/"
            "pdfmake/0.1.66/fonts/Roboto/Roboto-Regular.ttf"
        )

        try:

            r = requests.get(
                url,
                timeout=30
            )

            r.raise_for_status()

            with open(
                font_path,
                "wb"
            ) as f:

                f.write(
                    r.content
                )

        except Exception as e:

            st.error(
                f"Не удалось загрузить шрифт: {e}"
            )

            raise

    try:

        pdfmetrics.getFont(
            "OzonFont"
        )

    except KeyError:

        pdfmetrics.registerFont(
            TTFont(
                "OzonFont",
                font_path
            )
        )

    return "OzonFont"


font_name = load_font()


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================

def normalize_order(order):

    if not order:
        return ""

    return (
        str(order)
        .strip()
        .lower()
        .replace("і", "i")
        .replace("І", "i")
    )


def get_short_code(order):

    """
    78277691-0407-1 -> 7691

    Именно этот код используется
    как номер этикетки.
    """

    order_norm = normalize_order(
        order
    )

    if "-" in order_norm:

        first_part = (
            order_norm
            .split("-")[0]
        )

        return first_part[-4:]

    return order_norm[-4:]


def get_numeric_key(order):

    return re.sub(
        r"\D",
        "",
        normalize_order(order)
    )


# ============================================================
# ПОИСК ОТПРАВЛЕНИЙ
# ============================================================

ORDER_PATTERN = re.compile(
    r"(\d{8,15}-\d{4}-\d+|[a-zA-Z]{0,4}\d{10,15})",
    re.IGNORECASE
)


def find_orders(text):

    if not text:
        return []

    return ORDER_PATTERN.findall(
        text
    )


# ============================================================
# ОЧИСТКА НОМЕРОВ
# ============================================================

def remove_order_numbers(
    text,
    orders
):

    if not text:
        return ""

    result = text

    for order in orders:

        order_norm = normalize_order(
            order
        )

        result = re.sub(
            re.escape(order_norm),
            " ",
            result,
            flags=re.IGNORECASE
        )

        result = re.sub(
            re.escape(str(order)),
            " ",
            result,
            flags=re.IGNORECASE
        )

    return result


# ============================================================
# НОРМАЛИЗАЦИЯ СТРОК PDF
# ============================================================

def normalize_lines(text):

    if not text:
        return []

    text = text.replace(
        "\xa0",
        " "
    )

    text = re.sub(
        r"Кол\s*[-–—]\s*во",
        "Кол-во",
        text,
        flags=re.IGNORECASE
    )

    result = []

    for line in text.splitlines():

        line = re.sub(
            r"\s+",
            " ",
            line
        ).strip()

        if line:

            result.append(
                line
            )

    return result


# ============================================================
# ПОИСК ЗНАЧЕНИЯ ПОСЛЕ ЗАГОЛОВКА
# ============================================================

def get_value_after_header(
    lines,
    header_pattern,
    stop_headers=None
):

    stop_headers = stop_headers or []

    for i, line in enumerate(lines):

        if not re.fullmatch(
            header_pattern,
            line,
            flags=re.IGNORECASE
        ):
            continue

        # ----------------------------------------------------
        # Следующие строки после заголовка
        # ----------------------------------------------------

        for j in range(
            i + 1,
            len(lines)
        ):

            candidate = lines[j].strip()

            if not candidate:
                continue

            # Если дошли до следующего заголовка —
            # значения нет.
            is_stop = False

            for stop_pattern in stop_headers:

                if re.fullmatch(
                    stop_pattern,
                    candidate,
                    flags=re.IGNORECASE
                ):

                    is_stop = True
                    break

            if is_stop:
                break

            return candidate

    return ""


# ============================================================
# ПОЛУЧЕНИЕ ТОВАРА / АРТИКУЛА / QTY / ЭТИКЕТКИ
# ============================================================

def extract_block_fields(
    text,
    orders
):

    """
    Жёстко разбирает один блок.

    Наша целевая структура:

        Товар
        НАЗВАНИЕ

        Артикул
        АРТИКУЛ

        Кол-во
        QTY

        Этикетка
        7691

    Возвращает:

        {
            name,
            article,
            qty,
            label
        }
    """

    if not text:

        return {
            "name": "Товар",
            "article": "-",
            "qty": "1",
            "label": ""
        }

    # --------------------------------------------------------
    # Строки
    # --------------------------------------------------------

    lines = normalize_lines(
        text
    )

    # --------------------------------------------------------
    # Удаляем номера отправлений
    # --------------------------------------------------------

    cleaned_lines = []

    for line in lines:

        current = line

        for order in orders:

            # Полный номер
            current = re.sub(
                re.escape(
                    str(order)
                ),
                " ",
                current,
                flags=re.IGNORECASE
            )

            # Нормализованный номер
            current = re.sub(
                re.escape(
                    normalize_order(order)
                ),
                " ",
                current,
                flags=re.IGNORECASE
            )

        current = re.sub(
            r"\s+",
            " ",
            current
        ).strip()

        if current:

            cleaned_lines.append(
                current
            )

    lines = cleaned_lines

    # ========================================================
    # НАХОДИМ ЗАГОЛОВКИ
    # ========================================================

    product_idx = -1
    article_idx = -1
    qty_idx = -1
    label_idx = -1

    for i, line in enumerate(
        lines
    ):

        if product_idx == -1 and re.fullmatch(
            r"Товар",
            line,
            flags=re.IGNORECASE
        ):

            product_idx = i

        if article_idx == -1 and re.fullmatch(
            r"Артикул",
            line,
            flags=re.IGNORECASE
        ):

            article_idx = i

        if qty_idx == -1 and re.fullmatch(
            r"Кол-во",
            line,
            flags=re.IGNORECASE
        ):

            qty_idx = i

        if label_idx == -1 and re.fullmatch(
            r"Этикетка",
            line,
            flags=re.IGNORECASE
        ):

            label_idx = i

    # ========================================================
    # НОМЕР ЭТИКЕТКИ
    # ========================================================

    label = ""

    if label_idx >= 0:

        # Ищем значение после Этикетка
        for j in range(
            label_idx + 1,
            min(
                label_idx + 5,
                len(lines)
            )
        ):

            candidate = lines[j].strip()

            if not candidate:
                continue

            # Номер этикетки — обычно 4 цифры
            m = re.search(
                r"(?<!\d)(\d{4})(?!\d)",
                candidate
            )

            if m:

                label = m.group(1)

                break

    # ========================================================
    # ЕСЛИ ЭТИКЕТКА НЕ НАЙДЕНА
    #
    # Пытаемся найти хвост отправления.
    # ========================================================

    if not label and orders:

        label = get_short_code(
            orders[0]
        )

    # ========================================================
    # АРТИКУЛ
    # ========================================================

    article = "-"

    if (
        article_idx >= 0
        and qty_idx > article_idx
    ):

        # Берём только область:
        #
        # Артикул
        # ↓
        # значение
        # ↓
        # Кол-во

        area = lines[
            article_idx + 1:
            qty_idx
        ]

        candidates = []

        for value in area:

            value = value.strip()

            if not value:
                continue

            # Если вдруг несколько значений
            # в одной строке
            tokens = re.findall(
                r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_\-./]*",
                value
            )

            for token in tokens:

                if len(token) < 2:
                    continue

                if token.isdigit():
                    continue

                candidates.append(
                    token
                )

        # ----------------------------------------------------
        # Предпочитаем значения,
        # содержащие цифры.
        #
        # MW1801 -> да
        # пластик -> нет
        # трубочка -> нет
        # ----------------------------------------------------

        numeric_articles = [
            x
            for x in candidates
            if re.search(
                r"\d",
                x
            )
        ]

        if numeric_articles:

            article = (
                numeric_articles[-1]
            )

        elif candidates:

            # Если артикул буквенный —
            # берём последнее значение.
            article = candidates[-1]

    # ========================================================
    # КОЛИЧЕСТВО
    # ========================================================

    qty = "1"

    if (
        qty_idx >= 0
        and label_idx > qty_idx
    ):

        area = lines[
            qty_idx + 1:
            label_idx
        ]

        for value in area:

            value = value.strip()

            # Строго число
            m = re.fullmatch(
                r"(\d{1,3})",
                value
            )

            if m:

                qty = m.group(1)

                break

            # Если PDF склеил:
            #
            # "1 шт"
            #
            m = re.search(
                r"(?<!\d)(\d{1,3})(?!\d)",
                value
            )

            if m:

                qty = m.group(1)

                break

    # ========================================================
    # НАЗВАНИЕ ТОВАРА
    # ========================================================

    name = "Товар"

    if (
        product_idx >= 0
        and article_idx > product_idx
    ):

        area = lines[
            product_idx + 1:
            article_idx
        ]

        name_parts = []

        for value in area:

            value = value.strip()

            if not value:
                continue

            # Не добавляем служебные значения
            if value.lower() in {
                "фото",
                "товар",
                "артикул",
                "кол-во",
                "этикетка",
                "ozon"
            }:

                continue

            name_parts.append(
                value
            )

        if name_parts:

            name = " ".join(
                name_parts
            )

    # ========================================================
    # НАЗВАНИЕ ОГРАНИЧИВАЕМ 20 СИМВОЛАМИ
    # ========================================================

    name = re.sub(
        r"\s+",
        " ",
        name
    ).strip()

    if len(name) > 20:

        name = name[:20].rstrip()

    if not name:

        name = "Товар"

    # ========================================================
    # ФИНАЛЬНАЯ ЗАЩИТА
    # ========================================================

    if not article:

        article = "-"

    if not qty:

        qty = "1"

    return {
        "name": name,
        "article": article,
        "qty": qty,
        "label": label
    }


# ============================================================
# ПАРСИНГ ЛИСТА ПОДБОРА
# ============================================================

def parse_assembly_list(
    pdf_file
):

    # --------------------------------------------------------
    # ГЛАВНЫЙ СЛОВАРЬ
    #
    # КЛЮЧ = НОМЕР ЭТИКЕТКИ
    #
    # Например:
    #
    # 7691 -> {
    #   article: MW1801,
    #   qty: 1,
    #   name: ...
    # }
    # --------------------------------------------------------

    label_map = {}

    # Дополнительный словарь по номеру отправления
    order_map = {}

    stats = {
        "pages": 0,
        "blocks": 0,
        "orders": 0,
        "matched_blocks": 0,
        "labels": 0
    }

    with pdfplumber.open(
        pdf_file
    ) as pdf:

        stats["pages"] = len(
            pdf.pages
        )

        progress = st.progress(
            0
        )

        status_text = st.empty()

        for page_index, page in enumerate(
            pdf.pages
        ):

            status_text.text(
                f"📄 Обработка листа подбора: "
                f"{page_index + 1}/{len(pdf.pages)}"
            )

            # =================================================
            # ГОРИЗОНТАЛЬНЫЕ ЛИНИИ
            # =================================================

            horizontal_lines = []

            for line in page.lines:

                try:

                    width = float(
                        line.get(
                            "width",
                            0
                        )
                    )

                    if width > 30:

                        horizontal_lines.append(
                            line
                        )

                except Exception:

                    continue

            horizontal_lines.sort(
                key=lambda x: x.get(
                    "top",
                    0
                )
            )

            # =================================================
            # КООРДИНАТЫ БЛОКОВ
            # =================================================

            y_coords = (
                [0]
                + [
                    line.get(
                        "top",
                        0
                    )
                    for line in horizontal_lines
                ]
                + [
                    page.height
                ]
            )

            y_coords = sorted(
                set(
                    round(
                        float(y),
                        2
                    )
                    for y in y_coords
                )
            )

            # =================================================
            # ОБРАБОТКА БЛОКОВ
            # =================================================

            for i in range(
                len(y_coords) - 1
            ):

                top = y_coords[i]

                bottom = y_coords[
                    i + 1
                ]

                if bottom - top < 15:

                    continue

                stats["blocks"] += 1

                bbox = (
                    0,
                    top,
                    page.width,
                    bottom
                )

                try:

                    crop = page.within_bbox(
                        bbox
                    )

                    text = crop.extract_text(
                        layout=True
                    )

                except Exception:

                    continue

                if not text:

                    continue

                # =================================================
                # ИЩЕМ НОМЕРА ОТПРАВЛЕНИЙ
                # =================================================

                orders_in_slice = find_orders(
                    text
                )

                if not orders_in_slice:

                    continue

                stats["orders"] += len(
                    orders_in_slice
                )

                # =================================================
                # РАЗБИРАЕМ БЛОК
                # =================================================

                fields = extract_block_fields(
                    text,
                    orders_in_slice
                )

                label = fields[
                    "label"
                ]

                article = fields[
                    "article"
                ]

                qty = fields[
                    "qty"
                ]

                name = fields[
                    "name"
                ]

                # =================================================
                # ЕСЛИ НАШЛИ НОМЕР ЭТИКЕТКИ
                # =================================================

                if label:

                    label_map[
                        label
                    ] = {
                        "name": name,
                        "article": article,
                        "qty": qty,
                        "label": label
                    }

                    stats["labels"] += 1

                # =================================================
                # ДОПОЛНИТЕЛЬНО СОХРАНЯЕМ ПО НОМЕРУ ОТПРАВЛЕНИЯ
                # =================================================

                item = {
                    "name": name,
                    "article": article,
                    "qty": qty,
                    "label": label
                }

                for order in orders_in_slice:

                    order_norm = normalize_order(
                        order
                    )

                    num_key = get_numeric_key(
                        order_norm
                    )

                    short_code = get_short_code(
                        order_norm
                    )

                    if num_key:

                        order_map[
                            num_key
                        ] = item

                    if short_code:

                        order_map[
                            short_code
                        ] = item

                    if len(num_key) >= 10:

                        order_map[
                            num_key[-10:]
                        ] = item

                    stats[
                        "matched_blocks"
                    ] += 1

            progress.progress(
                (page_index + 1)
                / len(pdf.pages)
            )

        status_text.text(
            f"✅ Лист подбора обработан: "
            f"{stats['pages']} стр."
        )

    # ========================================================
    # ВОЗВРАЩАЕМ ОБА СЛОВАРЯ
    # ========================================================

    return (
        label_map,
        order_map,
        stats
    )


# ============================================================
# СОЗДАНИЕ ИНФО-БЛОКА
# ============================================================

def create_info_label(
    width,
    height,
    order_number,
    product_info
):

    packet = BytesIO()

    c = canvas.Canvas(
        packet,
        pagesize=(
            width,
            height
        )
    )

    x_margin = 10

    # ========================================================
    # НОМЕР ЗАКАЗА
    # ========================================================

    c.setFont(
        font_name,
        10
    )

    c.drawString(
        x_margin,
        height - 18,
        f"Заказ: {order_number}"
    )

    c.line(
        x_margin,
        height - 20,
        width - x_margin,
        height - 20
    )

    # ========================================================
    # АРТИКУЛ
    # ========================================================

    c.setFont(
        font_name,
        12
    )

    article = str(
        product_info.get(
            "article",
            "-"
        )
    )

    if len(article) > 25:

        article = (
            article[:22]
            + "..."
        )

    c.drawString(
        x_margin,
        height - 35,
        f"Арт: {article}"
    )

    # ========================================================
    # НАЗВАНИЕ
    # ========================================================

    name = str(
        product_info.get(
            "name",
            "Товар не найден"
        )
    )

    # ========================================================
    # РАЗМЕР НАЗВАНИЯ
    # ========================================================

    top_limit = (
        height - 52
    )

    bottom_limit = 50

    available_h = (
        top_limit
        - bottom_limit
    )

    current_size = 10

    line_h = 12

    def get_lines(
        txt,
        chars
    ):

        words = txt.split()

        result = []

        current = ""

        for word in words:

            if (
                len(current)
                + len(word)
                < chars
            ):

                current += (
                    word
                    + " "
                )

            else:

                if current:

                    result.append(
                        current.strip()
                    )

                current = (
                    word
                    + " "
                )

        if current:

            result.append(
                current.strip()
            )

        return result

    lines = get_lines(
        name,
        32
    )

    while (
        len(lines)
        * line_h
        > available_h
        and current_size > 6
    ):

        current_size -= 0.5

        line_h -= 0.6

        lines = get_lines(
            name,
            int(
                32
                * (
                    10
                    / current_size
                )
            )
        )

    # ========================================================
    # РИСУЕМ НАЗВАНИЕ
    # ========================================================

    c.setFont(
        font_name,
        current_size
    )

    y_text = top_limit

    for line in lines:

        if y_text > bottom_limit:

            c.drawString(
                x_margin,
                y_text,
                line
            )

            y_text -= line_h

    # ========================================================
    # КОЛИЧЕСТВО
    # ========================================================

    c.setFont(
        font_name,
        24
    )

    qty = product_info.get(
        "qty",
        "?"
    )

    c.drawString(
        x_margin,
        15,
        f"КОЛ-ВО: {qty}"
    )

    c.save()

    packet.seek(0)

    return PdfReader(
        packet
    ).pages[0]


# ============================================================
# ИНТЕРФЕЙС
# ============================================================

col1, col2 = st.columns(
    2
)

with col1:

    labels_file = st.file_uploader(
        "1️⃣ Этикетки (PDF)",
        type="pdf"
    )

with col2:

    assembly_file = st.file_uploader(
        "2️⃣ Лист подбора отправлений (PDF)",
        type="pdf"
    )


# ============================================================
# СКЛЕЙКА
# ============================================================

if labels_file and assembly_file:

    if st.button(
        "🚀 Склеить файлы",
        type="primary",
        use_container_width=True
    ):

        total_labels = 0

        success_count = 0

        error_orders = []

        # ====================================================
        # АНАЛИЗ
        # ====================================================

        with st.status(
            "Анализ и склейка...",
            expanded=True
        ) as status:

            st.write(
                "🔎 Читаю лист подбора..."
            )

            (
                label_map,
                order_map,
                assembly_stats
            ) = parse_assembly_list(
                assembly_file
            )

            st.write(
                f"📄 Страниц листа подбора: "
                f"{assembly_stats['pages']}"
            )

            st.write(
                f"📦 Найдено отправлений: "
                f"{assembly_stats['orders']}"
            )

            st.write(
                f"🏷️ Найдено номеров этикеток: "
                f"{len(label_map)}"
            )

            st.write(
                "🏷️ Читаю этикетки..."
            )

            reader = PdfReader(
                labels_file
            )

            writer = PdfWriter()

            total_labels = len(
                reader.pages
            )

            # =================================================
            # ОБРАБОТКА ЭТИКЕТОК
            # =================================================

            for i in range(
                total_labels
            ):

                page = reader.pages[i]

                # ------------------------------------------------
                # Оригинальная этикетка
                # ------------------------------------------------

                writer.add_page(
                    page
                )

                try:

                    text = (
                        page.extract_text()
                        or ""
                    )

                except Exception:

                    text = ""

                # =================================================
                # ОЧИСТКА ТЕКСТА
                # =================================================

                text_no_underscores = re.sub(
                    r"_\d+",
                    "",
                    text
                )

                clean_text = re.sub(
                    r"\s+",
                    "",
                    text_no_underscores
                )

                clean_text_norm = (
                    clean_text
                    .lower()
                    .replace(
                        "і",
                        "i"
                    )
                    .replace(
                        "І",
                        "i"
                    )
                )

                # =================================================
                # ИЩЕМ НОМЕР ОТПРАВЛЕНИЯ
                # =================================================

                order_match = re.search(
                    ORDER_PATTERN,
                    clean_text_norm
                )

                w = float(
                    page.mediabox.width
                )

                h = float(
                    page.mediabox.height
                )

                if order_match:

                    full_num = (
                        order_match.group(1)
                    )

                    # =================================================
                    # ГЛАВНЫЙ КЛЮЧ
                    #
                    # Номер этикетки
                    # =================================================

                    label_code = get_short_code(
                        full_num
                    )

                    # =================================================
                    # ЖЁСТКИЙ ПОИСК ПО НОМЕРУ ЭТИКЕТКИ
                    # =================================================

                    info = label_map.get(
                        label_code
                    )

                    # =================================================
                    # FALLBACK:
                    # полный номер отправления
                    # =================================================

                    if not info:

                        num_key = get_numeric_key(
                            full_num
                        )

                        info = order_map.get(
                            num_key
                        )

                        if (
                            not info
                            and len(num_key) >= 10
                        ):

                            info = order_map.get(
                                num_key[-10:]
                            )

                    # =================================================
                    # ОТОБРАЖАЕМЫЙ НОМЕР
                    # =================================================

                    display_num = (
                        full_num.upper()
                    )

                    if display_num.startswith(
                        "II"
                    ):

                        display_num = (
                            "ii"
                            + display_num[2:]
                        )

                    # =================================================
                    # НЕ НАШЛИ
                    # =================================================

                    if not info:

                        info = {
                            "name":
                                "Товар не найден",

                            "article":
                                "-",

                            "qty":
                                "?",

                            "label":
                                label_code
                        }

                        error_orders.append(
                            display_num
                        )

                    else:

                        # Защита количества
                        current_qty = str(
                            info.get(
                                "qty",
                                "1"
                            )
                        )

                        if (
                            current_qty
                            == label_code
                        ):

                            info = dict(
                                info
                            )

                            info["qty"] = "1"

                        success_count += 1

                    # =================================================
                    # ИНФОРМАЦИОННАЯ СТРАНИЦА
                    # =================================================

                    writer.add_page(
                        create_info_label(
                            w,
                            h,
                            display_num,
                            info
                        )
                    )

                else:

                    writer.add_page(
                        create_info_label(
                            w,
                            h,
                            "???",
                            {
                                "name":
                                    "Номер не распознан",

                                "article":
                                    "-",

                                "qty":
                                    "-"
                            }
                        )
                    )

                    error_orders.append(
                        "Неизвестный номер на этикетке"
                    )

            status.update(
                label="✅ Обработка завершена!",
                state="complete"
            )

        # ====================================================
        # ДИАГНОСТИКА
        # ====================================================

        st.divider()

        col_m1, col_m2, col_m3 = st.columns(
            3
        )

        col_m1.metric(
            "Всего этикеток",
            total_labels
        )

        col_m2.metric(
            "Успешно привязано",
            success_count
        )

        col_m3.metric(
            "Ошибок",
            total_labels - success_count
        )

        # ====================================================
        # РЕЗУЛЬТАТ
        # ====================================================

        if success_count == total_labels:

            st.success(
                "✅ Все этикетки сопоставлены "
                "по номеру этикетки."
            )

        else:

            st.error(
                f"⚠️ Не удалось найти "
                f"{total_labels - success_count} "
                f"товар(ов)."
            )

            if error_orders:

                st.write(
                    "Проблемные отправления:"
                )

                max_errors_to_show = 100

                for err in error_orders[
                    :max_errors_to_show
                ]:

                    st.markdown(
                        f"- **{err}**"
                    )

                if (
                    len(error_orders)
                    > max_errors_to_show
                ):

                    st.caption(
                        f"... и ещё "
                        f"{len(error_orders) - max_errors_to_show}"
                    )

        # ====================================================
        # СОХРАНЕНИЕ PDF
        # ====================================================

        output = BytesIO()

        writer.write(
            output
        )

        output.seek(0)

        st.download_button(
            "📥 Скачать PDF для печати",
            output,
            "Ready_Labels.pdf",
            "application/pdf",
            type="primary",
            use_container_width=True
        )

