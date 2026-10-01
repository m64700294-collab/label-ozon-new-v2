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
    "Сервис нарезает лист подбора по слоям и сопоставляет "
    "товары с этикетками Ozon."
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

    # Не регистрируем повторно
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
# НОРМАЛИЗАЦИЯ НОМЕРОВ
# ============================================================

def normalize_order(order):
    """
    Нормализация номера отправления.
    """

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
    Возвращает последние 4 цифры первой части номера.

    Например:

    78277691-0407-1 -> 7691
    902053221026000 -> 0000
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
    """
    Только цифры из номера.
    """

    order_norm = normalize_order(
        order
    )

    return re.sub(
        r"\D",
        "",
        order_norm
    )


# ============================================================
# ПОИСК НОМЕРОВ ОТПРАВЛЕНИЙ
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
# УДАЛЕНИЕ НОМЕРА ОТПРАВЛЕНИЯ И ЕГО ХВОСТА
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

        # Полный номер
        result = re.sub(
            re.escape(order_norm),
            " ",
            result,
            flags=re.IGNORECASE
        )

        # Исходный вариант номера
        result = re.sub(
            re.escape(str(order)),
            " ",
            result,
            flags=re.IGNORECASE
        )

        # Хвост номера
        short_code = get_short_code(
            order
        )

        if short_code:

            result = re.sub(
                rf"(?<!\d){re.escape(short_code)}(?!\d)",
                " ",
                result
            )

    return result


# ============================================================
# ОЧИСТКА ТЕКСТА
# ============================================================

def clean_assembly_text(
    text,
    orders
):

    if not text:
        return ""

    result = text

    # Удаляем номера отправлений
    result = remove_order_numbers(
        result,
        orders
    )

    # Стандартные заголовки Ozon
    headers = [
        r"Склад МСК ООО.*?",
        r"Склад:.*?",
        r"Служба доставки:.*?",
        r"Номер отправления",
        r"Номер с этикетки",
        r"Количество отправлений",
        r"Дата:",
        r"Фото",
        r"Товар",
        r"Артикул",
        r"Кол-во",
        r"Этикетка",
        r"Ozon",
        r"Проверьте список.*?отменять их\.",
        r"№"
    ]

    for pattern in headers:

        result = re.sub(
            pattern,
            " ",
            result,
            flags=re.IGNORECASE
        )

    result = result.replace(
        "|",
        " "
    )

    # Убираем служебные строки с номерами
    result = re.sub(
        r"(?m)^\s*(?:\d+\s+)+",
        " ",
        result
    )

    # Убираем повторяющиеся пробелы
    result = re.sub(
        r"[ \t]+",
        " ",
        result
    )

    return result.strip()


# ============================================================
# СТРОГИЙ РАЗБОР ТОВАРА
# ============================================================

def extract_product_fields(
    text,
    forbidden_codes=None
):
    """
    Строго извлекает:

        название товара
        артикул
        количество

    из структуры Ozon:

        Товар
        НАЗВАНИЕ ТОВАРА

        Артикул
        АРТИКУЛ

        Кол-во
        КОЛИЧЕСТВО

        Этикетка
        КОД ЭТИКЕТКИ

    ВАЖНО:

    - цифры из названия товара НЕ используются как qty;
    - слова из названия товара НЕ используются как article;
    - код этикетки НЕ используется как qty.
    """

    if not text:

        return (
            "Товар",
            "-",
            "1"
        )

    forbidden_codes = (
        forbidden_codes or set()
    )

    # ========================================================
    # НОРМАЛИЗАЦИЯ
    # ========================================================

    text = text.replace(
        "\xa0",
        " "
    )

    # Унифицируем:
    #
    # Кол-во
    # Кол - во
    # Кол–во
    # Кол — во
    #
    text = re.sub(
        r"Кол\s*[-–—]\s*во",
        "Кол-во",
        text,
        flags=re.IGNORECASE
    )

    # ========================================================
    # ПОЛУЧАЕМ СТРОКИ
    # ========================================================

    lines = []

    for line in text.splitlines():

        line = line.replace(
            "\xa0",
            " "
        )

        line = re.sub(
            r"\s+",
            " ",
            line
        ).strip()

        if line:

            lines.append(
                line
            )

    # ========================================================
    # УДАЛЯЕМ НОМЕРА И ХВОСТЫ
    #
    # Только для дальнейшего анализа.
    # Исходный text выше сохраняется.
    # ========================================================

    cleaned_lines = []

    for line in lines:

        current = line

        for code in forbidden_codes:

            if not code:
                continue

            current = re.sub(
                rf"(?<!\d){re.escape(code)}(?!\d)",
                " ",
                current
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
    # ВСПОМОГАТЕЛЬНЫЙ ПОИСК ЗАГОЛОВКА
    # ========================================================

    def find_header(
        pattern
    ):

        for index, line in enumerate(
            lines
        ):

            if re.fullmatch(
                pattern,
                line,
                flags=re.IGNORECASE
            ):

                return index

        return -1

    # ========================================================
    # ИЩЕМ ЗАГОЛОВКИ
    # ========================================================

    product_idx = find_header(
        r"Товар"
    )

    article_idx = find_header(
        r"Артикул"
    )

    qty_idx = find_header(
        r"Кол-во"
    )

    label_idx = find_header(
        r"Этикетка"
    )

    # ========================================================
    # НАЗВАНИЕ ТОВАРА
    # ========================================================

    name = "Товар"

    if (
        product_idx >= 0
        and article_idx > product_idx
    ):

        name_area = lines[
            product_idx + 1:
            article_idx
        ]

        name_area = [
            x.strip()
            for x in name_area
            if x.strip()
        ]

        if name_area:

            # Удаляем возможные служебные строки
            filtered_name = []

            for value in name_area:

                low = value.lower().strip()

                if low in {
                    "фото",
                    "товар",
                    "артикул",
                    "кол-во",
                    "этикетка",
                    "номер отправления",
                    "номер с этикетки",
                    "ozon"
                }:

                    continue

                filtered_name.append(
                    value
                )

            if filtered_name:

                name = " ".join(
                    filtered_name
                )

    # ========================================================
    # АРТИКУЛ
    # ========================================================

    article = "-"

    if (
        article_idx >= 0
        and qty_idx > article_idx
    ):

        article_area = lines[
            article_idx + 1:
            qty_idx
        ]

        article_area = [
            x.strip()
            for x in article_area
            if x.strip()
        ]

        # ----------------------------------------------------
        # В нормальном Ozon PDF здесь должно быть:
        #
        # MW1801
        #
        # поэтому сначала проверяем строки целиком.
        # ----------------------------------------------------

        for candidate in article_area:

            candidate = candidate.strip()

            if not candidate:
                continue

            # Убираем служебные символы
            candidate = re.sub(
                r"^[|:]+",
                "",
                candidate
            )

            candidate = re.sub(
                r"[|:]+$",
                "",
                candidate
            )

            candidate = candidate.strip()

            if not candidate:
                continue

            # Служебные значения
            if candidate.lower() in {
                "товар",
                "артикул",
                "кол-во",
                "этикетка",
                "фото",
                "ozon"
            }:

                continue

            # Запрещённые коды
            if candidate in forbidden_codes:
                continue

            # ------------------------------------------------
            # ВАЖНАЯ ЗАЩИТА
            #
            # Не принимаем длинную фразу:
            #
            # "Пластиковая трубочка для воды"
            #
            # как артикул.
            # ------------------------------------------------

            if len(candidate.split()) > 3:

                continue

            if len(candidate) > 60:

                continue

            # ------------------------------------------------
            # Артикул обычно имеет:
            #
            # MW1801
            # MW020702_1
            # ABC-123
            # 123ABC
            # ------------------------------------------------

            if not re.fullmatch(
                r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_\-./]*",
                candidate
            ):

                continue

            # Не принимаем чисто цифровое значение
            if candidate.isdigit():

                continue

            article = candidate

            break

    # ========================================================
    # КОЛИЧЕСТВО
    # ========================================================

    qty = "1"

    if (
        qty_idx >= 0
        and label_idx > qty_idx
    ):

        qty_area = lines[
            qty_idx + 1:
            label_idx
        ]

        for candidate in qty_area:

            candidate = candidate.strip()

            # Количество должно быть строго числом
            match = re.fullmatch(
                r"(\d{1,3})",
                candidate
            )

            if not match:
                continue

            value = match.group(
                1
            )

            # Хвост номера запрещён
            if value in forbidden_codes:

                continue

            try:

                number = int(
                    value
                )

                if 1 <= number <= 999:

                    qty = value

                    break

            except Exception:

                continue

    # ========================================================
    # ЕСЛИ PDF СЛЕПИЛ СТРОКИ
    #
    # Например:
    #
    # Артикул MW1801 Кол-во 3 Этикетка 7691
    #
    # ========================================================

    if article == "-":

        flat = " ".join(
            lines
        )

        # Сначала пытаемся найти участок:
        #
        # Артикул ... Кол-во
        #

        article_match = re.search(
            r"\bАртикул\b"
            r"\s*(.*?)"
            r"\s*\bКол-во\b",
            flat,
            flags=re.IGNORECASE
        )

        if article_match:

            area = (
                article_match
                .group(1)
                .strip()
            )

            # Если внутри одна нормальная строка
            candidates = re.findall(
                r"[A-Za-zА-Яа-я0-9]"
                r"[A-Za-zА-Яа-я0-9_\-./]*",
                area
            )

            # Берём последнее подходящее значение
            for candidate in reversed(
                candidates
            ):

                if candidate in forbidden_codes:
                    continue

                if candidate.isdigit():
                    continue

                if len(candidate) < 2:
                    continue

                # Не используем слова,
                # которые явно являются частью служебного текста
                if candidate.lower() in {
                    "товар",
                    "фото",
                    "артикул",
                    "ozon"
                }:

                    continue

                article = candidate

                break

    # ========================================================
    # СЛИТНОЕ КОЛИЧЕСТВО
    #
    # Только внутри:
    #
    # Кол-во ... Этикетка
    #
    # ========================================================

    if qty == "1":

        flat = " ".join(
            lines
        )

        qty_match = re.search(
            r"\bКол-во\b"
            r"\s*(.*?)"
            r"\s*\bЭтикетка\b",
            flat,
            flags=re.IGNORECASE
        )

        if qty_match:

            qty_area = (
                qty_match
                .group(1)
            )

            numbers = re.findall(
                r"(?<!\d)(\d{1,3})(?!\d)",
                qty_area
            )

            for value in numbers:

                if value in forbidden_codes:
                    continue

                try:

                    number = int(
                        value
                    )

                    if 1 <= number <= 999:

                        qty = value

                        break

                except Exception:

                    continue

    # ========================================================
    # ФИНАЛЬНАЯ ЗАЩИТА
    # ========================================================

    if not name:

        name = "Товар"

    if not article:

        article = "-"

    if not qty:

        qty = "1"

    if qty in forbidden_codes:

        qty = "1"

    return (
        name,
        article,
        str(qty)
    )


# ============================================================
# ПАРСИНГ ЛИСТА ПОДБОРА
# ============================================================

def parse_assembly_list(
    pdf_file
):

    data = {}

    stats = {
        "pages": 0,
        "blocks": 0,
        "orders": 0,
        "matched_blocks": 0
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

            lines = []

            for line in page.lines:

                try:

                    width = float(
                        line.get(
                            "width",
                            0
                        )
                    )

                    if width > 30:

                        lines.append(
                            line
                        )

                except Exception:

                    continue

            lines.sort(
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
                    for line in lines
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
            # ОБРАБОТКА КАЖДОГО БЛОКА
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
                # ИЩЕМ ОТПРАВЛЕНИЯ
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
                # ЗАПРЕЩЁННЫЕ КОДЫ
                # =================================================

                forbidden_codes = set()

                for order in orders_in_slice:

                    code = get_short_code(
                        order
                    )

                    if code:

                        forbidden_codes.add(
                            code
                        )

                # =================================================
                # СТРОГО РАЗБИРАЕМ ИСХОДНЫЙ ТЕКСТ
                #
                # Здесь НЕ используем text_clean,
                # потому что text_clean удаляет:
                #
                # Товар
                # Артикул
                # Кол-во
                # Этикетка
                #
                # А нам эти заголовки нужны.
                # =================================================

                name_text, article, qty = (
                    extract_product_fields(
                        text,
                        forbidden_codes
                    )
                )

                # =================================================
                # ДОПОЛНИТЕЛЬНАЯ ОЧИСТКА НАЗВАНИЯ
                # ОТ ХВОСТА НОМЕРА
                # =================================================

                for code in forbidden_codes:

                    name_text = re.sub(
                        rf"(?<!\d){re.escape(code)}(?!\d)",
                        " ",
                        name_text
                    )

                name_text = re.sub(
                    r"\s+",
                    " ",
                    name_text
                ).strip()

                if (
                    not name_text
                    or len(name_text) < 2
                ):

                    name_text = "Товар"

                # =================================================
                # СОХРАНЯЕМ
                # =================================================

                item = {
                    "name": name_text,
                    "article": article,
                    "qty": qty
                }

                # =================================================
                # ЗАПИСЫВАЕМ ДАННЫЕ ДЛЯ КАЖДОГО ОТПРАВЛЕНИЯ
                # =================================================

                for order in orders_in_slice:

                    order_norm = normalize_order(
                        order
                    )

                    code = get_short_code(
                        order_norm
                    )

                    num_key = get_numeric_key(
                        order_norm
                    )

                    # Основной короткий ключ
                    if code:

                        data[code] = item

                    # Полный цифровой ключ
                    if num_key:

                        data[num_key] = item

                    # Последние 10 цифр
                    if len(num_key) >= 10:

                        data[
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

    return (
        data,
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

    article = product_info.get(
        "article",
        "-"
    )

    article = str(
        article
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

    name = product_info.get(
        "name",
        "Товар не найден"
    )

    name = str(
        name
    )

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
            # ЭТИКЕТКИ
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

                # =================================================
                # ПОИСК НОМЕРА
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

                    short_code = get_short_code(
                        full_num
                    )

                    # =================================================
                    # ПОИСК ИНФОРМАЦИИ
                    # =================================================

                    info = assembly_data.get(
                        short_code
                    )

                    if not info:

                        num_key = get_numeric_key(
                            full_num
                        )

                        info = assembly_data.get(
                            num_key
                        )

                        if (
                            not info
                            and len(num_key) >= 10
                        ):

                            info = assembly_data.get(
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
                    # ТОВАР НЕ НАЙДЕН
                    # =================================================

                    if not info:

                        info = {
                            "name":
                                "Товар не найден",

                            "article":
                                "-",

                            "qty":
                                "?"
                        }

                        error_orders.append(
                            display_num
                        )

                    else:

                        # =================================================
                        # ФИНАЛЬНАЯ ЗАЩИТА ОТ ОШИБОЧНОГО QTY
                        # =================================================

                        forbidden_code = (
                            get_short_code(
                                full_num
                            )
                        )

                        current_qty = str(
                            info.get(
                                "qty",
                                "1"
                            )
                        )

                        if (
                            current_qty
                            == forbidden_code
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
                "✅ Все товары идеально сопоставлены! "
                "Можно печатать."
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

