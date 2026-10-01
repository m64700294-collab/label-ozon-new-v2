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
    "Сервис сопоставляет этикетки Ozon с листом подбора "
    "и добавляет информацию о товаре."
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

            response = requests.get(
                url,
                timeout=30
            )

            response.raise_for_status()

            with open(
                font_path,
                "wb"
            ) as f:

                f.write(
                    response.content
                )

        except Exception as e:

            st.error(
                f"Не удалось загрузить шрифт: {e}"
            )

            raise

    try:

        pdfmetrics.registerFont(
            TTFont(
                "OzonFont",
                font_path
            )
        )

    except Exception:
        # Если шрифт уже зарегистрирован
        pass

    return "OzonFont"


font_name = load_font()


# ============================================================
# НОМЕРА ОТПРАВЛЕНИЙ
# ============================================================

ORDER_PATTERN = re.compile(
    r"(\d{8,15}-\d{4}-\d+|[a-zA-Z]{0,4}\d{10,15})",
    re.IGNORECASE
)


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

    order = normalize_order(order)

    if "-" in order:

        first_part = order.split("-")[0]

        return first_part[-4:]

    return order[-4:]


def get_numeric_key(order):

    order = normalize_order(order)

    return re.sub(
        r"\D",
        "",
        order
    )


def find_orders(text):

    if not text:
        return []

    return ORDER_PATTERN.findall(text)


# ============================================================
# ОЧИСТКА СЛУЖЕБНЫХ СИМВОЛОВ
# ============================================================

def normalize_pdf_text(text):

    if not text:
        return ""

    # Неразрывные пробелы
    text = text.replace("\xa0", " ")

    # Разные тире
    text = (
        text
        .replace("–", "-")
        .replace("—", "-")
        .replace("-", "-")
    )

    # Убираем лишние пробелы в строках
    lines = []

    for line in text.splitlines():

        line = re.sub(
            r"[ \t]+",
            " ",
            line
        ).strip()

        if line:
            lines.append(line)

    return "\n".join(lines)


# ============================================================
# ИЗВЛЕЧЕНИЕ ПОЛЕЙ ИЗ СТРУКТУРЫ OZON
#
# Структура:
#
# Номер отправления
# 78277691-0407-1
#
# Фото Товар
# Антикоррозионное покрытие...
#
# Артикул
# MW1801
#
# Кол-во
# 1
#
# Этикетка
# 7691
# ============================================================

def extract_structured_product(text, orders_in_slice):

    if not text:

        return {
            "name": "Товар",
            "article": "-",
            "qty": "1"
        }

    text = normalize_pdf_text(text)

    # --------------------------------------------------------
    # Удаляем номера отправлений из текста
    # --------------------------------------------------------

    text_without_orders = text

    for order in orders_in_slice:

        text_without_orders = re.sub(
            re.escape(order),
            " ",
            text_without_orders,
            flags=re.IGNORECASE
        )

    # --------------------------------------------------------
    # Разбиваем на строки
    # --------------------------------------------------------

    lines = [
        line.strip()
        for line in text_without_orders.splitlines()
        if line.strip()
    ]

    # --------------------------------------------------------
    # Находим позиции служебных заголовков
    # --------------------------------------------------------

    def find_line_index(pattern):

        regex = re.compile(
            pattern,
            re.IGNORECASE
        )

        for index, line in enumerate(lines):

            if regex.search(line):

                return index

        return -1

    article_index = find_line_index(
        r"^Артикул$"
    )

    qty_index = find_line_index(
        r"^Кол[--]?во$"
    )

    label_index = find_line_index(
        r"^Этикетка$"
    )

    # --------------------------------------------------------
    # Иногда pdfplumber объединяет:
    #
    # "Фото Товар"
    #
    # поэтому ищем также отдельное слово Товар
    # --------------------------------------------------------

    product_index = find_line_index(
        r"(?:^|\s)Товар(?:\s|$)"
    )

    # ========================================================
    # АРТИКУЛ
    # ========================================================

    article = "-"

    if article_index >= 0:

        for j in range(
            article_index + 1,
            min(
                article_index + 4,
                len(lines)
            )
        ):

            candidate = lines[j].strip()

            if not candidate:
                continue

            # Если сразу пошёл следующий заголовок,
            # артикул не найден
            if re.fullmatch(
                r"(Кол[--]?во|Этикетка|Товар|Фото)",
                candidate,
                re.IGNORECASE
            ):
                break

            # Артикул обычно одна строка
            article = candidate

            break

    # ========================================================
    # КОЛИЧЕСТВО
    #
    # КРИТИЧЕСКИЙ МОМЕНТ:
    #
    # Берём ТОЛЬКО значение между:
    #
    # Кол-во
    #
    # и
    #
    # Этикетка
    #
    # Поэтому 7691 никогда не попадёт сюда.
    # ========================================================

    qty = "1"

    if qty_index >= 0:

        qty_end = (
            label_index
            if label_index > qty_index
            else min(
                qty_index + 4,
                len(lines)
            )
        )

        qty_candidates = []

        for j in range(
            qty_index + 1,
            qty_end
        ):

            candidate = lines[j].strip()

            # Только чистое число
            if re.fullmatch(
                r"\d{1,3}",
                candidate
            ):

                try:

                    value = int(candidate)

                    if 1 <= value <= 999:

                        qty_candidates.append(
                            str(value)
                        )

                except Exception:
                    pass

        if qty_candidates:

            # Обычно здесь будет ровно одно значение
            qty = qty_candidates[0]

    # ========================================================
    # НАЗВАНИЕ ТОВАРА
    # ========================================================

    name = "Товар"

    if product_index >= 0 and article_index > product_index:

        name_parts = []

        for j in range(
            product_index + 1,
            article_index
        ):

            line = lines[j].strip()

            if not line:
                continue

            # Не добавляем служебные заголовки
            if re.fullmatch(
                r"(Фото|Товар)",
                line,
                re.IGNORECASE
            ):
                continue

            name_parts.append(line)

        if name_parts:

            name = " ".join(
                name_parts
            )

    # --------------------------------------------------------
    # Если Товар/Фото были объединены в одну строку,
    # пытаемся найти текст между "Товар" и "Артикул".
    # --------------------------------------------------------

    if (
        name == "Товар"
        and article_index >= 0
    ):

        joined_text = "\n".join(lines)

        match = re.search(
            r"(?:Фото\s+)?Товар\s+(.+?)\s+Артикул",
            joined_text,
            flags=re.IGNORECASE | re.DOTALL
        )

        if match:

            possible_name = re.sub(
                r"\s+",
                " ",
                match.group(1)
            ).strip()

            if possible_name:

                name = possible_name

    # --------------------------------------------------------
    # Финальная очистка
    # --------------------------------------------------------

    name = re.sub(
        r"\s+",
        " ",
        name
    ).strip()

    article = re.sub(
        r"\s+",
        " ",
        article
    ).strip()

    # Если каким-то образом артикул стал служебным словом
    if article.lower() in {
        "кол-во",
        "кол-во",
        "этикетка",
        "товар",
        "фото"
    }:

        article = "-"

    # --------------------------------------------------------
    # Защита qty
    # --------------------------------------------------------

    if not re.fullmatch(
        r"\d{1,3}",
        str(qty)
    ):

        qty = "1"

    return {
        "name": name or "Товар",
        "article": article or "-",
        "qty": qty
    }


# ============================================================
# PARSE ЛИСТА ПОДБОРА
# ============================================================

def parse_assembly_list(pdf_file):

    data = {}

    stats = {
        "pages": 0,
        "blocks": 0,
        "orders": 0,
        "products": 0
    }

    with pdfplumber.open(pdf_file) as pdf:

        stats["pages"] = len(pdf.pages)

        progress = st.progress(
            0
        )

        status_text = st.empty()

        for page_index, page in enumerate(pdf.pages):

            status_text.text(
                f"📄 Обработка листа подбора: "
                f"{page_index + 1}/{len(pdf.pages)}"
            )

            # ------------------------------------------------
            # Ищем горизонтальные разделители
            # ------------------------------------------------

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

            # ------------------------------------------------
            # Координаты блоков
            # ------------------------------------------------

            y_coords = [0]

            for line in horizontal_lines:

                try:

                    y_coords.append(
                        float(
                            line.get(
                                "top",
                                0
                            )
                        )
                    )

                except Exception:
                    pass

            y_coords.append(
                float(
                    page.height
                )
            )

            # Удаляем дубли
            y_coords = sorted(
                set(
                    round(
                        y,
                        2
                    )
                    for y in y_coords
                )
            )

            # ------------------------------------------------
            # Обработка блоков
            # ------------------------------------------------

            for i in range(
                len(y_coords) - 1
            ):

                top = y_coords[i]
                bottom = y_coords[i + 1]

                if (
                    bottom - top
                    < 15
                ):
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

                # ------------------------------------------------
                # Ищем номер отправления
                # ------------------------------------------------

                orders_in_slice = find_orders(
                    text
                )

                if not orders_in_slice:
                    continue

                stats["orders"] += len(
                    orders_in_slice
                )

                # ------------------------------------------------
                # Извлекаем структурированные данные
                # ------------------------------------------------

                item = extract_structured_product(
                    text,
                    orders_in_slice
                )

                # ------------------------------------------------
                # Записываем каждый заказ
                # ------------------------------------------------

                for order in orders_in_slice:

                    order_norm = normalize_order(
                        order
                    )

                    short_code = get_short_code(
                        order_norm
                    )

                    numeric_key = get_numeric_key(
                        order_norm
                    )

                    # --------------------------------------------
                    # Основной короткий ключ
                    # --------------------------------------------

                    if short_code:

                        data[
                            short_code
                        ] = item

                    # --------------------------------------------
                    # Полный цифровой номер
                    # --------------------------------------------

                    if numeric_key:

                        data[
                            numeric_key
                        ] = item

                    # --------------------------------------------
                    # Последние 10 цифр
                    # --------------------------------------------

                    if len(numeric_key) >= 10:

                        data[
                            numeric_key[-10:]
                        ] = item

                    stats["products"] += 1

            progress.progress(
                (page_index + 1)
                / max(
                    len(pdf.pages),
                    1
                )
            )

        status_text.text(
            f"✅ Лист подбора обработан: "
            f"{stats['pages']} стр."
        )

    return data, stats


# ============================================================
# СОЗДАНИЕ ИНФОРМАЦИОННОЙ СТРАНИЦЫ
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

    # --------------------------------------------------------
    # Номер заказа
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Артикул
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Название
    # --------------------------------------------------------

    name = str(
        product_info.get(
            "name",
            "Товар не найден"
        )
    )

    top_limit = height - 52
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

    # --------------------------------------------------------
    # Название
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Количество
    # --------------------------------------------------------

    c.setFont(
        font_name,
        24
    )

    qty = str(
        product_info.get(
            "qty",
            "1"
        )
    )

    # Финальная защита
    if not re.fullmatch(
        r"\d{1,3}",
        qty
    ):

        qty = "1"

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

col1, col2 = st.columns(2)

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

        with st.status(
            "Анализ и склейка...",
            expanded=True
        ) as status:

            # ------------------------------------------------
            # ЛИСТ ПОДБОРА
            # ------------------------------------------------

            st.write(
                "🔎 Читаю лист подбора..."
            )

            assembly_data, assembly_stats = (
                parse_assembly_list(
                    assembly_file
                )
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
                f"🔑 Индексов сопоставления: "
                f"{len(assembly_data)}"
            )

            # ------------------------------------------------
            # ЭТИКЕТКИ
            # ------------------------------------------------

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

            # ------------------------------------------------
            # ОБХОД ЭТИКЕТОК
            # ------------------------------------------------

            for i in range(
                total_labels
            ):

                page = reader.pages[i]

                # Сохраняем оригинальную этикетку
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

                # ------------------------------------------------
                # Очистка текста этикетки
                # ------------------------------------------------

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

                # ------------------------------------------------
                # Ищем номер отправления
                # ------------------------------------------------

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

                # =================================================
                # НОМЕР НАЙДЕН
                # =================================================

                if order_match:

                    full_num = (
                        order_match.group(1)
                    )

                    short_code = get_short_code(
                        full_num
                    )

                    # ------------------------------------------------
                    # Ищем товар
                    # ------------------------------------------------

                    info = assembly_data.get(
                        short_code
                    )

                    if not info:

                        numeric_key = get_numeric_key(
                            full_num
                        )

                        info = assembly_data.get(
                            numeric_key
                        )

                        if (
                            not info
                            and len(numeric_key) >= 10
                        ):

                            info = assembly_data.get(
                                numeric_key[-10:]
                            )

                    # ------------------------------------------------
                    # Номер для отображения
                    # ------------------------------------------------

                    display_num = full_num.upper()

                    if display_num.startswith(
                        "II"
                    ):

                        display_num = (
                            "ii"
                            + display_num[2:]
                        )

                    # =================================================
                    # ТОВАР НЕ НАЙДЕН
                    # =================================================

                    if not info:

                        info = {
                            "name": "Товар не найден",
                            "article": "-",
                            "qty": "?"
                        }

                        error_orders.append(
                            display_num
                        )

                    # =================================================
                    # ТОВАР НАЙДЕН
                    # =================================================

                    else:

                        # --------------------------------------------
                        # Дополнительная защита.
                        #
                        # Если почему-то qty равен 4-значному хвосту
                        # номера — заменяем на 1.
                        # --------------------------------------------

                        info = dict(
                            info
                        )

                        current_qty = str(
                            info.get(
                                "qty",
                                "1"
                            )
                        )

                        if (
                            current_qty
                            == short_code
                        ):

                            info["qty"] = "1"

                        success_count += 1

                    # ------------------------------------------------
                    # Добавляем информационную страницу
                    # ------------------------------------------------

                    writer.add_page(
                        create_info_label(
                            w,
                            h,
                            display_num,
                            info
                        )
                    )

                # =================================================
                # НОМЕР НЕ НАЙДЕН
                # =================================================

                else:

                    writer.add_page(
                        create_info_label(
                            w,
                            h,
                            "???",
                            {
                                "name":
                                    "Номер не распознан",
                                "article": "-",
                                "qty": "-"
                            }
                        )
                    )

                    error_orders.append(
                        "Неизвестный номер "
                        "на этикетке"
                    )

            status.update(
                label="✅ Обработка завершена!",
                state="complete"
            )

        # ====================================================
        # СТАТИСТИКА
        # ====================================================

        st.divider()

        col_m1, col_m2, col_m3 = st.columns(3)

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
            total_labels
            - success_count
        )

        # ====================================================
        # РЕЗУЛЬТАТ
        # ====================================================

        if success_count == total_labels:

            st.success(
                "✅ Все товары идеально "
                "сопоставлены! Можно печатать."
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

                max_errors = 100

                for err in error_orders[
                    :max_errors
                ]:

                    st.markdown(
                        f"- **{err}**"
                    )

                if len(error_orders) > max_errors:

                    st.caption(
                        f"... и ещё "
                        f"{len(error_orders) - max_errors}"
                    )

        # ====================================================
        # ФИНАЛЬНЫЙ PDF
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
