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
    "Склейка этикеток Ozon с листом подбора "
    "по графическим строкам и коду этикетки."
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

        r = requests.get(
            url,
            timeout=30
        )

        r.raise_for_status()

        with open(
            font_path,
            "wb"
        ) as f:
            f.write(r.content)

    try:
        pdfmetrics.getFont("OzonFont")
    except Exception:
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

def normalize_text(value):

    if value is None:
        return ""

    value = str(value)

    value = (
        value
        .replace("\xa0", " ")
        .replace("і", "i")
        .replace("І", "i")
    )

    return value.strip()


def normalize_order(value):

    value = normalize_text(value)

    value = (
        value
        .lower()
        .replace(" ", "")
        .replace("\t", "")
    )

    return value


# ============================================================
# КОД ЭТИКЕТКИ
# ============================================================

def get_label_code(value):

    value = normalize_order(value)

    if not value:
        return ""

    # --------------------------------------------------------
    # Стандартный номер:
    #
    # 78277691-0407-1
    #
    # -> 7691
    # --------------------------------------------------------

    if "-" in value:

        first_part = value.split("-")[0]

        digits = re.sub(
            r"\D",
            "",
            first_part
        )

        if len(digits) >= 4:
            return digits[-4:]

    # --------------------------------------------------------
    # ii50103202537
    #
    # -> 2537
    # --------------------------------------------------------

    digits = re.sub(
        r"\D",
        "",
        value
    )

    if len(digits) >= 4:
        return digits[-4:]

    return value[-4:]


# ============================================================
# ПОЛНЫЙ ЧИСЛОВОЙ КЛЮЧ
# ============================================================

def get_numeric_key(value):

    value = normalize_order(value)

    return re.sub(
        r"\D",
        "",
        value
    )


# ============================================================
# ПОИСК НОМЕРОВ
# ============================================================

ORDER_PATTERN = re.compile(
    r"""
    (?:
        \d{8,15}
        \s*
        -
        \s*
        \d{4}
        \s*
        -
        \s*
        \d+
    )
    |
    (?:
        (?:ii|i)?\d{10,15}
    )
    """,
    re.IGNORECASE |
    re.VERBOSE
)


def find_orders(text):

    if not text:
        return []

    result = []

    for match in ORDER_PATTERN.finditer(text):

        value = match.group(0)

        value = re.sub(
            r"\s+",
            "",
            value
        )

        result.append(value)

    return result


# ============================================================
# СОЕДИНЕНИЕ РАЗОРВАННОГО НОМЕРА
# ============================================================

def normalize_extracted_order(text):

    if not text:
        return ""

    value = normalize_text(text)

    # 78277691 -0407-1
    value = re.sub(
        r"(\d{8,15})\s*-\s*(\d{4})\s*-\s*(\d+)",
        r"\1-\2-\3",
        value
    )

    # ii5010320 2537
    value = re.sub(
        r"(ii\d{6,15})\s+(\d{4})",
        r"\1\2",
        value,
        flags=re.IGNORECASE
    )

    return value


# ============================================================
# ПОИСК НОМЕРА НА ЭТИКЕТКЕ
# ============================================================

def extract_order_from_label(text):

    if not text:
        return None

    text = normalize_extracted_order(
        text
    )

    # --------------------------------------------------------
    # Сначала стандартный Ozon
    # --------------------------------------------------------

    m = re.search(
        r"\d{8,15}-\d{4}-\d+",
        text
    )

    if m:
        return m.group(0)

    # --------------------------------------------------------
    # ii + номер
    # --------------------------------------------------------

    m = re.search(
        r"ii\d{10,16}",
        text,
        flags=re.IGNORECASE
    )

    if m:
        return m.group(0)

    # --------------------------------------------------------
    # Просто длинный номер
    # --------------------------------------------------------

    numbers = re.findall(
        r"\d{10,15}",
        text
    )

    if numbers:

        # Предпочитаем длиннейший
        numbers.sort(
            key=len,
            reverse=True
        )

        return numbers[0]

    return None


# ============================================================
# ГОРИЗОНТАЛЬНЫЕ ЛИНИИ
# ============================================================

def get_horizontal_lines(page):

    result = []

    for line in page.lines:

        try:

            width = float(
                line.get(
                    "width",
                    0
                )
            )

            height = abs(
                float(
                    line.get(
                        "height",
                        0
                    )
                )
            )

            # Горизонтальная линия:
            # большая ширина
            # очень маленькая высота

            if width > 30 and height < 3:

                result.append(
                    line
                )

        except Exception:
            continue

    result.sort(
        key=lambda x: float(
            x.get(
                "top",
                0
            )
        )
    )

    return result


# ============================================================
# КООРДИНАТЫ СТРОК
# ============================================================

def get_row_boxes(page):

    horizontal_lines = get_horizontal_lines(
        page
    )

    y_coords = [0]

    for line in horizontal_lines:

        try:
            y = float(
                line.get(
                    "top",
                    0
                )
            )

            y_coords.append(
                y
            )

        except Exception:
            pass

    y_coords.append(
        float(page.height)
    )

    # Удаляем дубликаты

    y_coords = sorted(
        set(
            round(
                y,
                2
            )
            for y in y_coords
        )
    )

    boxes = []

    for i in range(
        len(y_coords) - 1
    ):

        top = y_coords[i]
        bottom = y_coords[i + 1]

        if bottom - top < 15:
            continue

        boxes.append(
            (
                0,
                top,
                float(page.width),
                bottom
            )
        )

    return boxes


# ============================================================
# ОПРЕДЕЛЕНИЕ КОЛОНОК
# ============================================================

def detect_columns(page):

    """
    Автоматически определяет вертикальные зоны таблицы.

    Для твоего формата:

    №
    Номер отправления
    Фото
    Товар
    Артикул
    Кол-во
    Этикетка
    """

    width = float(
        page.width
    )

    # --------------------------------------------------------
    # ВАЖНО:
    #
    # Эти значения являются относительными.
    # Они не зависят от конкретного размера страницы.
    # --------------------------------------------------------

    columns = {

        "number": (
            0.00 * width,
            0.08 * width
        ),

        "order": (
            0.08 * width,
            0.29 * width
        ),

        "photo": (
            0.29 * width,
            0.39 * width
        ),

        "product": (
            0.39 * width,
            0.72 * width
        ),

        "article": (
            0.72 * width,
            0.84 * width
        ),

        "qty": (
            0.84 * width,
            0.92 * width
        ),

        "label": (
            0.92 * width,
            1.00 * width
        )
    }

    return columns


# ============================================================
# ПОИСК ТЕКСТА В КОЛОНКЕ
# ============================================================

def extract_column_text(
    page,
    bbox,
    x1,
    x2
):

    top = bbox[1]
    bottom = bbox[3]

    # Небольшой запас,
    # чтобы не терять текст на границах

    margin = 2

    column_bbox = (
        max(
            0,
            x1 - margin
        ),
        top,
        min(
            page.width,
            x2 + margin
        ),
        bottom
    )

    try:

        crop = page.crop(
            column_bbox
        )

        text = crop.extract_text(
            layout=False
        )

        if not text:
            return ""

        return normalize_text(
            text
        )

    except Exception:
        return ""


# ============================================================
# ИЗВЛЕЧЕНИЕ ТЕКСТА ПО X-КООРДИНАТАМ
# ============================================================

def extract_words_by_x(
    page,
    bbox
):

    try:

        words = page.extract_words(
            x_tolerance=2,
            y_tolerance=3,
            keep_blank_chars=False
        )

    except Exception:
        return []

    result = []

    for word in words:

        wx1 = float(
            word.get(
                "x0",
                0
            )
        )

        wx2 = float(
            word.get(
                "x1",
                0
            )
        )

        wy1 = float(
            word.get(
                "top",
                0
            )
        )

        wy2 = float(
            word.get(
                "bottom",
                0
            )
        )

        # Проверяем попадание слова
        # внутрь строки

        if wy2 < bbox[1]:
            continue

        if wy1 > bbox[3]:
            continue

        result.append(
            {
                "text": word.get(
                    "text",
                    ""
                ),
                "x0": wx1,
                "x1": wx2,
                "top": wy1,
                "bottom": wy2
            }
        )

    return result


# ============================================================
# ОПРЕДЕЛЕНИЕ КОЛОНКИ СЛОВА
# ============================================================

def word_in_column(
    word,
    column
):

    x1 = word["x0"]
    x2 = word["x1"]

    c1 = column[0]
    c2 = column[1]

    center = (
        x1 + x2
    ) / 2

    return (
        center >= c1
        and center <= c2
    )


# ============================================================
# ТЕКСТ КОЛОНКИ ЧЕРЕЗ СЛОВА
# ============================================================

def get_column_from_words(
    words,
    column
):

    selected = []

    for word in words:

        if word_in_column(
            word,
            column
        ):

            selected.append(
                word
            )

    selected.sort(
        key=lambda x: (
            x["top"],
            x["x0"]
        )
    )

    return " ".join(
        word["text"]
        for word in selected
    ).strip()


# ============================================================
# ОЧИСТКА АРТИКУЛА
# ============================================================

def clean_article(value):

    if not value:
        return "-"

    value = normalize_text(
        value
    )

    value = re.sub(
        r"\s+",
        "",
        value
    )

    # Убираем мусор вокруг

    value = value.strip(
        "|:;,. "
    )

    # Если случайно попало несколько
    # значений, выбираем наиболее похожее
    # на артикул.

    tokens = re.findall(
        r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_.\-/]*",
        value
    )

    if not tokens:
        return "-"

    # Предпочитаем значение с буквами

    for token in tokens:

        if re.search(
            r"[A-Za-zА-Яа-я]",
            token
        ):

            return token

    return tokens[0]


# ============================================================
# ОЧИСТКА КОЛИЧЕСТВА
# ============================================================

def clean_qty(value):

    if not value:
        return "1"

    value = normalize_text(
        value
    )

    numbers = re.findall(
        r"(?<!\d)\d{1,4}(?!\d)",
        value
    )

    if not numbers:
        return "1"

    # Берём первое число именно
    # из колонки Кол-во.

    for number in numbers:

        try:

            qty = int(
                number
            )

            if 1 <= qty <= 9999:

                return str(
                    qty
                )

        except Exception:
            continue

    return "1"


# ============================================================
# ОЧИСТКА НАЗВАНИЯ
# ============================================================

def clean_product_name(value):

    if not value:
        return "Товар"

    value = normalize_text(
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    ).strip()

    # --------------------------------------------------------
    # Оставляем максимум 20 символов,
    # как ты просил.
    # --------------------------------------------------------

    if len(value) > 20:

        value = value[:20].rstrip() + "..."

    return value


# ============================================================
# РАЗБОР ОДНОЙ СТРОКИ
# ============================================================

def parse_row(
    page,
    bbox,
    columns
):

    # Получаем слова с координатами

    words = extract_words_by_x(
        page,
        bbox
    )

    if not words:
        return None

    # --------------------------------------------------------
    # Получаем содержимое колонок
    # --------------------------------------------------------

    order_text = get_column_from_words(
        words,
        columns["order"]
    )

    product_text = get_column_from_words(
        words,
        columns["product"]
    )

    article_text = get_column_from_words(
        words,
        columns["article"]
    )

    qty_text = get_column_from_words(
        words,
        columns["qty"]
    )

    label_text = get_column_from_words(
        words,
        columns["label"]
    )

    # --------------------------------------------------------
    # Иногда PDF неправильно разбивает номер.
    # Пробуем также обычный текст строки.
    # --------------------------------------------------------

    full_row_text = " ".join(
        word["text"]
        for word in words
    )

    order_text = (
        order_text
        + " "
        + full_row_text
    )

    # --------------------------------------------------------
    # Ищем номер отправления
    # --------------------------------------------------------

    orders = find_orders(
        order_text
    )

    # --------------------------------------------------------
    # Если стандартный номер не найден,
    # пробуем внутренний номер.
    # --------------------------------------------------------

    if not orders:

        m = re.search(
            r"(?:ii)?\d{10,16}",
            full_row_text,
            flags=re.IGNORECASE
        )

        if m:
            orders = [
                m.group(0)
            ]

    # --------------------------------------------------------
    # Код этикетки
    # --------------------------------------------------------

    label_digits = re.findall(
        r"\d{4}",
        label_text
    )

    label_code = ""

    if label_digits:

        # Берём последнее 4-значное
        # значение в колонке Этикетка

        label_code = label_digits[-1]

    # --------------------------------------------------------
    # Если номер найден,
    # нормализуем
    # --------------------------------------------------------

    normalized_orders = []

    for order in orders:

        order = normalize_extracted_order(
            order
        )

        if order:
            normalized_orders.append(
                order
            )

    # --------------------------------------------------------
    # Если вообще нет номера и этикетки,
    # это скорее всего заголовок/служебная строка
    # --------------------------------------------------------

    if (
        not normalized_orders
        and not label_code
    ):

        return None

    # --------------------------------------------------------
    # Артикул
    # --------------------------------------------------------

    article = clean_article(
        article_text
    )

    # --------------------------------------------------------
    # Количество
    # --------------------------------------------------------

    qty = clean_qty(
        qty_text
    )

    # --------------------------------------------------------
    # Название
    # --------------------------------------------------------

    product = clean_product_name(
        product_text
    )

    return {
        "orders": normalized_orders,
        "label_code": label_code,
        "article": article,
        "qty": qty,
        "name": product
    }


# ============================================================
# СОХРАНЕНИЕ В МАППИНГ
# ============================================================

def save_mapping(
    data,
    row
):

    item = {
        "name": row["name"],
        "article": row["article"],
        "qty": row["qty"]
    }

    # --------------------------------------------------------
    # По коду Этикетка
    # --------------------------------------------------------

    label_code = row.get(
        "label_code",
        ""
    )

    if label_code:

        # Отдельный словарь кодов
        # будет создан вызывающей функцией

        data["labels"][
            label_code
        ] = item

    # --------------------------------------------------------
    # По номеру отправления
    # --------------------------------------------------------

    for order in row.get(
        "orders",
        []
    ):

        normalized = normalize_order(
            order
        )

        if not normalized:
            continue

        data["orders"][
            normalized
        ] = item

        numeric = get_numeric_key(
            normalized
        )

        if numeric:

            data["numeric"][
                numeric
            ] = item

            if len(numeric) >= 10:

                data["numeric"][
                    numeric[-10:]
                ] = item


# ============================================================
# ПАРСИНГ ЛИСТА ПОДБОРА
# ============================================================

def parse_assembly_list(
    pdf_file
):

    data = {
        "orders": {},
        "numeric": {},
        "labels": {}
    }

    stats = {
        "pages": 0,
        "blocks": 0,
        "rows": 0,
        "orders": 0,
        "articles": 0,
        "products": 0,
        "qty": 0,
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
                f"📄 Лист подбора: "
                f"{page_index + 1}/"
                f"{len(pdf.pages)}"
            )

            # ------------------------------------------------
            # ГЛАВНОЕ:
            # СТРОКИ ОПРЕДЕЛЯЮТСЯ ПО ГРАФИЧЕСКИМ ЛИНИЯМ
            # ------------------------------------------------

            row_boxes = get_row_boxes(
                page
            )

            columns = detect_columns(
                page
            )

            for bbox in row_boxes:

                stats["blocks"] += 1

                row = parse_row(
                    page,
                    bbox,
                    columns
                )

                if not row:
                    continue

                # Не считаем заголовок строкой заказа

                has_order = bool(
                    row["orders"]
                )

                has_label = bool(
                    row["label_code"]
                )

                if not has_order and not has_label:
                    continue

                stats["rows"] += 1

                if row["orders"]:

                    stats["orders"] += len(
                        row["orders"]
                    )

                if (
                    row["article"]
                    and row["article"] != "-"
                ):

                    stats["articles"] += 1

                if (
                    row["name"]
                    and row["name"] != "Товар"
                ):

                    stats["products"] += 1

                if row["qty"]:

                    stats["qty"] += 1

                if row["label_code"]:

                    stats["labels"] += 1

                save_mapping(
                    data,
                    row
                )

            progress.progress(
                (page_index + 1)
                /
                len(pdf.pages)
            )

    status_text.text(
        "✅ Лист подбора обработан"
    )

    return data, stats


# ============================================================
# ПОИСК ТОВАРА ПО НОМЕРУ
# ============================================================

def find_info(
    mapping,
    order
):

    normalized = normalize_order(
        order
    )

    # --------------------------------------------------------
    # 1. Прямой номер
    # --------------------------------------------------------

    info = mapping["orders"].get(
        normalized
    )

    if info:
        return info, "номер"

    # --------------------------------------------------------
    # 2. Цифровой номер
    # --------------------------------------------------------

    numeric = get_numeric_key(
        normalized
    )

    if numeric:

        info = mapping["numeric"].get(
            numeric
        )

        if info:
            return info, "цифровой номер"

        # ----------------------------------------------------
        # 3. Последние 10 цифр
        # ----------------------------------------------------

        if len(numeric) >= 10:

            info = mapping["numeric"].get(
                numeric[-10:]
            )

            if info:
                return info, "последние 10"

    # --------------------------------------------------------
    # 4. Последние 4 цифры
    # --------------------------------------------------------

    code = get_label_code(
        normalized
    )

    if code:

        info = mapping["labels"].get(
            code
        )

        if info:
            return info, "этикетка 4"

    return None, None


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
    # Номер
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

    top_limit = (
        height - 52
    )

    bottom_limit = 50

    available_h = (
        top_limit
        -
        bottom_limit
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
                +
                len(word)
                <
                chars
            ):

                current += (
                    word
                    +
                    " "
                )

            else:

                if current:

                    result.append(
                        current.strip()
                    )

                current = (
                    word
                    +
                    " "
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
        len(lines) * line_h
        >
        available_h
        and
        current_size > 6
    ):

        current_size -= 0.5

        line_h -= 0.6

        lines = get_lines(
            name,
            int(
                32 *
                (
                    10
                    /
                    current_size
                )
            )
        )

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
# ОБРАБОТКА
# ============================================================

if (
    labels_file
    and
    assembly_file
):

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
                "🔎 Читаю лист подбора "
                "по горизонтальным линиям..."
            )

            assembly_data, stats = (
                parse_assembly_list(
                    assembly_file
                )
            )

            st.write(
                f"📄 Страниц: "
                f"{stats['pages']}"
            )

            st.write(
                f"📦 Строк заказов: "
                f"{stats['rows']}"
            )

            st.write(
                f"🔢 Номеров: "
                f"{stats['orders']}"
            )

            st.write(
                f"🏷️ Артикулов: "
                f"{stats['articles']}"
            )

            st.write(
                f"📦 Товаров: "
                f"{stats['products']}"
            )

            st.write(
                f"🔢 Количеств: "
                f"{stats['qty']}"
            )

            st.write(
                f"🏷️ Кодов этикеток: "
                f"{stats['labels']}"
            )

            st.write(
                f"🔑 Индексов номера: "
                f"{len(assembly_data['orders'])}"
            )

            st.write(
                f"🔑 Индексов этикетки: "
                f"{len(assembly_data['labels'])}"
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
            # ОБРАБОТКА ЭТИКЕТОК
            # ------------------------------------------------

            for i in range(
                total_labels
            ):

                page = reader.pages[i]

                # Оригинальная этикетка

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
                # Ищем номер
                # ------------------------------------------------

                full_num = (
                    extract_order_from_label(
                        text
                    )
                )

                w = float(
                    page.mediabox.width
                )

                h = float(
                    page.mediabox.height
                )

                if full_num:

                    info, method = find_info(
                        assembly_data,
                        full_num
                    )

                    # ------------------------------------------------
                    # Если номер не найден,
                    # пробуем напрямую последние 4
                    # ------------------------------------------------

                    if not info:

                        label_code = get_label_code(
                            full_num
                        )

                        info = (
                            assembly_data[
                                "labels"
                            ].get(
                                label_code
                            )
                        )

                        if info:

                            method = (
                                "резерв: "
                                "4 цифры"
                            )

                    display_num = (
                        full_num.upper()
                    )

                    if display_num.startswith(
                        "II"
                    ):

                        display_num = (
                            "ii"
                            +
                            display_num[2:]
                        )

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
                            (
                                i + 1,
                                display_num
                            )
                        )

                    else:

                        success_count += 1

                    writer.add_page(
                        create_info_label(
                            w,
                            h,
                            display_num,
                            info
                        )
                    )

                else:

                    # ------------------------------------------------
                    # Номер не распознан
                    # ------------------------------------------------

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
                        (
                            i + 1,
                            "Номер не распознан"
                        )
                    )

            status.update(
                label="✅ Обработка завершена!",
                state="complete"
            )

        # ====================================================
        # ДИАГНОСТИКА
        # ====================================================

        st.divider()

        st.subheader(
            "📊 Диагностика"
        )

        col_m1, col_m2, col_m3 = (
            st.columns(3)
        )

        col_m1.metric(
            "Всего этикеток",
            total_labels
        )

        col_m2.metric(
            "Успешно",
            success_count
        )

        col_m3.metric(
            "Не найдено",
            total_labels
            -
            success_count
        )

        # ----------------------------------------------------
        # Диагностика листа
        # ----------------------------------------------------

        st.write(
            "### 📋 Лист подбора"
        )

        d1, d2, d3, d4 = st.columns(
            4
        )

        d1.metric(
            "Строк",
            stats["rows"]
        )

        d2.metric(
            "Артикулов",
            stats["articles"]
        )

        d3.metric(
            "Количеств",
            stats["qty"]
        )

        d4.metric(
            "Этикеток",
            stats["labels"]
        )

        # ----------------------------------------------------
        # Ошибки
        # ----------------------------------------------------

        if (
            success_count
            ==
            total_labels
        ):

            st.success(
                "✅ Все этикетки успешно "
                "сопоставлены!"
            )

        else:

            st.error(
                f"⚠️ Не найдено: "
                f"{total_labels - success_count}"
            )

            if error_orders:

                st.write(
                    "### ❌ Проблемные этикетки"
                )

                for page_number, order in (
                    error_orders[:100]
                ):

                    st.markdown(
                        f"- Страница "
                        f"**{page_number}** — "
                        f"**{order}**"
                    )

                if len(
                    error_orders
                ) > 100:

                    st.caption(
                        f"... ещё "
                        f"{len(error_orders) - 100}"
                    )

        # ====================================================
        # СКАЧИВАНИЕ
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
