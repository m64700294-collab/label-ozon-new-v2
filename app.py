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
    page_title="Ozon FBS — Этикетки + Лист подбора",
    page_icon="🖨️",
    layout="wide"
)

st.title("🖨️ Ozon FBS — Этикетки + Лист подбора")

st.write(
    "Маппинг: номер отправления → номер с этикетки → товар → артикул → количество"
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

        response = requests.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        with open(font_path, "wb") as f:
            f.write(response.content)

    try:
        pdfmetrics.registerFont(
            TTFont(
                "OzonFont",
                font_path
            )
        )
    except Exception:
        pass

    return "OzonFont"


font_name = load_font()


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================

def normalize_shipment(value):

    if not value:
        return ""

    value = str(value)

    value = re.sub(
        r"\s+",
        "",
        value
    )

    value = (
        value
        .replace("І", "I")
        .replace("і", "i")
    )

    return value.lower()


# ============================================================
# ПОЛУЧАЕМ ГОРИЗОНТАЛЬНЫЕ ЛИНИИ
# ============================================================

def get_horizontal_lines(page):

    result = []

    page_width = float(page.width)

    # --------------------------------------------------------
    # Обычные линии PDF
    # --------------------------------------------------------

    for line in page.lines:

        x0 = float(
            line.get("x0", 0)
        )

        x1 = float(
            line.get("x1", 0)
        )

        y0 = float(
            line.get("y0", 0)
        )

        y1 = float(
            line.get("y1", 0)
        )

        # Только горизонтальные
        if abs(y0 - y1) > 1:
            continue

        width = abs(x1 - x0)

        # НЕ 70%.
        # У Ozon линия может быть короче ширины страницы.
        if width < page_width * 0.40:
            continue

        top = float(page.height) - y0

        result.append(top)


    # --------------------------------------------------------
    # Иногда PDF хранит линии как rects
    # --------------------------------------------------------

    for rect in page.rects:

        x0 = float(
            rect.get("x0", 0)
        )

        x1 = float(
            rect.get("x1", 0)
        )

        y0 = float(
            rect.get("y0", 0)
        )

        y1 = float(
            rect.get("y1", 0)
        )

        width = abs(x1 - x0)
        height = abs(y1 - y0)

        # Очень тонкий горизонтальный прямоугольник
        if height <= 2 and width >= page_width * 0.40:

            top = float(page.height) - y0

            result.append(top)


    # --------------------------------------------------------
    # Удаляем дубли
    # --------------------------------------------------------

    result.sort()

    unique = []

    for y in result:

        if not unique:

            unique.append(y)

        elif abs(y - unique[-1]) > 2:

            unique.append(y)


    return unique


# ============================================================
# ТЕКСТ МЕЖДУ ГОРИЗОНТАЛЬНЫМИ ЛИНИЯМИ
# ============================================================

def get_text_between_lines(
    page,
    words,
    top,
    bottom
):

    row_words = []

    for word in words:

        word_top = float(
            word["top"]
        )

        word_bottom = float(
            word["bottom"]
        )

        # С небольшим запасом
        if (
            word_top >= top + 1
            and
            word_bottom <= bottom + 1
        ):

            row_words.append(
                word
            )


    if not row_words:
        return ""


    # --------------------------------------------------------
    # Очень важно:
    # сохраняем расположение текста по вертикали.
    # --------------------------------------------------------

    row_words.sort(
        key=lambda x: (
            round(float(x["top"]), 1),
            float(x["x0"])
        )
    )


    lines = []

    current_line = []

    current_y = None


    for word in row_words:

        y = float(
            word["top"]
        )

        if (
            current_y is None
            or
            abs(y - current_y) <= 3
        ):

            current_line.append(
                word
            )

        else:

            if current_line:

                current_line.sort(
                    key=lambda x: float(
                        x["x0"]
                    )
                )

                lines.append(
                    " ".join(
                        x["text"]
                        for x in current_line
                    )
                )

            current_line = [
                word
            ]

        current_y = y


    if current_line:

        current_line.sort(
            key=lambda x: float(
                x["x0"]
            )
        )

        lines.append(
            " ".join(
                x["text"]
                for x in current_line
            )
        )


    return "\n".join(
        lines
    )


# ============================================================
# УДАЛЕНИЕ СЛУЖЕБНОГО МУСОРА
# ============================================================

def clean_row_text(text):

    if not text:
        return ""


    # Убираем заголовки
    text = re.sub(
        r"\bФото\b",
        " ",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\bТовар\b",
        " ",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\bАртикул\b",
        " ",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\bКол-во\b",
        " ",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\bЭтикетка\b",
        " ",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# ПОИСК ХВОСТА:
#
# АРТИКУЛ → КОЛИЧЕСТВО → ЭТИКЕТКА
# ============================================================

def parse_product_tail(text):

    if not text:
        return None


    tokens = text.split()

    if len(tokens) < 3:
        return None


    # --------------------------------------------------------
    # Ищем 4 цифры справа налево
    # --------------------------------------------------------

    label_index = None
    label = None


    for i in range(
        len(tokens) - 1,
        -1,
        -1
    ):

        token = tokens[i].strip()


        # Нормальный случай
        if re.fullmatch(
            r"\d{4}",
            token
        ):

            label_index = i
            label = token

            break


    if label_index is None:

        # ----------------------------------------------------
        # Редкий случай:
        #
        # PDF склеил:
        #
        # 1 8886
        #
        # в:
        #
        # 18886
        # ----------------------------------------------------

        for i in range(
            len(tokens) - 1,
            -1,
            -1
        ):

            token = tokens[i].strip()

            if not re.fullmatch(
                r"\d{5,8}",
                token
            ):
                continue


            possible_label = token[-4:]
            possible_qty = token[:-4]


            if re.fullmatch(
                r"\d{1,4}",
                possible_qty
            ):

                label_index = i
                label = possible_label

                qty = possible_qty

                article_index = i - 1

                break

        else:

            return None


    # --------------------------------------------------------
    # Обычный вариант
    # --------------------------------------------------------

    if "qty" not in locals():

        qty_index = label_index - 1

        if qty_index < 0:
            return None


        qty = tokens[
            qty_index
        ]


        if not re.fullmatch(
            r"\d{1,4}",
            qty
        ):

            return None


        article_index = (
            qty_index - 1
        )


    if article_index < 0:
        return None


    article = tokens[
        article_index
    ]


    # --------------------------------------------------------
    # Иногда перед артикулом стоит № строки
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d+",
        article
    ):

        article_index -= 1

        if article_index < 0:
            return None

        article = tokens[
            article_index
        ]


    # --------------------------------------------------------
    # Название товара
    # --------------------------------------------------------

    name = " ".join(
        tokens[
            :article_index
        ]
    )


    # Убираем порядковый номер
    name = re.sub(
        r"^\s*\d+\s+",
        "",
        name
    )


    name = re.sub(
        r"\s+",
        " ",
        name
    ).strip()


    if not name:
        name = "Товар"


    return {

        "article": article,

        "qty": qty,

        "label": label,

        "name": name
    }


# ============================================================
# ПАРСИНГ ОДНОЙ ПОЗИЦИИ
# ============================================================

def parse_row(text):

    if not text:
        return None


    # --------------------------------------------------------
    # Номер отправления
    # --------------------------------------------------------

    shipment_matches = re.findall(
        r"\d{8,15}-\d{4}-\d+",
        text
    )


    if not shipment_matches:
        return None


    shipment = normalize_shipment(
        shipment_matches[0]
    )


    # --------------------------------------------------------
    # Убираем номер отправления
    # --------------------------------------------------------

    work = text

    for shipment_number in shipment_matches:

        work = work.replace(
            shipment_number,
            " "
        )


    # --------------------------------------------------------
    # Убираем ii500...
    #
    # ВАЖНО:
    # это номер с этикетки,
    # НЕ номер отправления.
    # --------------------------------------------------------

    work = re.sub(
        r"\b[iiіі]{2}\d{8,20}\b",
        " ",
        work,
        flags=re.IGNORECASE
    )


    work = clean_row_text(
        work
    )


    if not work:
        return None


    # --------------------------------------------------------
    # Разбираем хвост
    # --------------------------------------------------------

    product = parse_product_tail(
        work
    )


    if not product:
        return None


    return {

        "shipment": shipment,

        "label": product["label"],

        "name": product["name"],

        "article": product["article"],

        "qty": product["qty"]
    }


# ============================================================
# ОСНОВНОЙ ПАРСЕР
# ============================================================

@st.cache_data(show_spinner=False)
def parse_assembly_list(pdf_bytes):

    data = {}

    diagnostics = []


    pdf_stream = BytesIO(
        pdf_bytes
    )


    with pdfplumber.open(
        pdf_stream
    ) as pdf:


        total_pages = len(
            pdf.pages
        )


        for page_number, page in enumerate(
            pdf.pages,
            start=1
        ):


            # ==================================================
            # Горизонтальные линии
            # ==================================================

            lines = get_horizontal_lines(
                page
            )


            # --------------------------------------------------
            # Если линий мало — диагностика
            # --------------------------------------------------

            if len(lines) < 2:

                diagnostics.append({

                    "Страница":
                        page_number,

                    "Горизонтальных линий":
                        len(lines),

                    "Сформировано строк":
                        0,

                    "Найдено отправлений":
                        0
                })

                continue


            # ==================================================
            # Слова страницы
            # ==================================================

            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False
            )


            if not words:
                continue


            # ==================================================
            # Границы
            # ==================================================

            boundaries = []

            boundaries.append(
                0
            )


            for y in lines:

                if (
                    not boundaries
                    or
                    abs(
                        y -
                        boundaries[-1]
                    ) > 2
                ):

                    boundaries.append(
                        y
                    )


            boundaries.append(
                float(page.height)
            )


            page_rows = 0
            page_found = 0


            # ==================================================
            # Каждая область между горизонтальными линиями
            # ==================================================

            for i in range(
                len(boundaries) - 1
            ):

                top = boundaries[i]
                bottom = boundaries[i + 1]


                if bottom - top < 20:
                    continue


                row_text = get_text_between_lines(
                    page,
                    words,
                    top,
                    bottom
                )


                if not row_text:
                    continue


                page_rows += 1


                # ------------------------------------------------
                # Парсим позицию
                # ------------------------------------------------

                result = parse_row(
                    row_text
                )


                if not result:
                    continue


                page_found += 1


                shipment = result[
                    "shipment"
                ]


                # ------------------------------------------------
                # Не перезаписываем корректную запись
                # ------------------------------------------------

                if shipment not in data:

                    data[
                        shipment
                    ] = result


            diagnostics.append({

                "Страница":
                    page_number,

                "Горизонтальных линий":
                    len(lines),

                "Сформировано строк":
                    page_rows,

                "Найдено отправлений":
                    page_found
            })


    # ============================================================
    # Результат
    # ============================================================

    st.subheader(
        "📊 Результат разбора"
    )


    col1, col2, col3 = st.columns(3)


    with col1:

        st.metric(
            "Страниц",
            total_pages
        )


    with col2:

        st.metric(
            "Отправлений",
            len(data)
        )


    with col3:

        st.metric(
            "Ожидается",
            236
        )


    # ============================================================
    # Диагностика страниц
    # ============================================================

    with st.expander(
        "🔍 Диагностика страниц",
        expanded=False
    ):

        st.dataframe(
            diagnostics,
            use_container_width=True,
            hide_index=True
        )


    # ============================================================
    # Таблица маппинга
    # ============================================================

    if data:

        mapping = []


        for shipment, item in data.items():

            mapping.append({

                "№ отправления":
                    shipment,

                "№ с этикетки":
                    item["label"],

                "Артикул":
                    item["article"],

                "Кол-во":
                    item["qty"],

                "Товар":
                    item["name"]
            })


        with st.expander(
            f"🔗 Маппинг — {len(mapping)} отправлений",
            expanded=True
        ):

            st.dataframe(
                mapping,
                use_container_width=True,
                hide_index=True
            )


    else:

        st.error(
            "Лист подбора не распознан."
        )


    return data


# ============================================================
# НОМЕР ОТПРАВЛЕНИЯ НА ЭТИКЕТКЕ
# ============================================================

def extract_shipment_from_label(page):

    text = page.extract_text()


    if not text:
        return None


    # --------------------------------------------------------
    # Основной формат:
    #
    # 78277691-0407-1
    # --------------------------------------------------------

    match = re.search(
        r"\d{8,15}-\d{4}-\d+",
        text
    )


    if match:

        return normalize_shipment(
            match.group(0)
        )


    # --------------------------------------------------------
    # ii...
    # --------------------------------------------------------

    clean = re.sub(
        r"\s+",
        "",
        text
    )


    match = re.search(
        r"ii\d{8,20}",
        clean,
        flags=re.IGNORECASE
    )


    if match:

        return normalize_shipment(
            match.group(0)
        )


    return None


# ============================================================
# ИНФОРМАЦИОННАЯ СТРАНИЦА
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


    margin = 10


    # --------------------------------------------------------
    # Заказ
    # --------------------------------------------------------

    c.setFont(
        font_name,
        9
    )


    c.drawString(
        margin,
        height - 16,
        f"Заказ: {order_number}"
    )


    c.line(
        margin,
        height - 20,
        width - margin,
        height - 20
    )


    # --------------------------------------------------------
    # Номер с этикетки
    # --------------------------------------------------------

    label = product_info.get(
        "label",
        "-"
    )


    c.setFont(
        font_name,
        9
    )


    c.drawString(
        margin,
        height - 32,
        f"Этикетка: {label}"
    )


    # --------------------------------------------------------
    # Артикул
    # --------------------------------------------------------

    article = product_info.get(
        "article",
        "-"
    )


    c.setFont(
        font_name,
        11
    )


    c.drawString(
        margin,
        height - 47,
        f"Арт: {article}"
    )


    # --------------------------------------------------------
    # Название
    # --------------------------------------------------------

    name = product_info.get(
        "name",
        "Товар не найден"
    )


    top = height - 63
    bottom = 48


    font_size = 9
    line_height = 11


    def split_text(
        text,
        chars
    ):

        words = text.split()

        result = []

        current = ""


        for word in words:

            test = (
                current +
                word +
                " "
            )


            if len(test) <= chars:

                current = test

            else:

                if current:

                    result.append(
                        current.strip()
                    )

                current = (
                    word +
                    " "
                )


        if current:

            result.append(
                current.strip()
            )


        return result


    lines = split_text(
        name,
        30
    )


    while (
        len(lines) * line_height
        > top - bottom
        and
        font_size > 6
    ):

        font_size -= 0.5
        line_height -= 0.5

        lines = split_text(
            name,
            max(
                20,
                int(
                    30 *
                    9 /
                    font_size
                )
            )
        )


    c.setFont(
        font_name,
        font_size
    )


    y = top


    for line in lines:

        if y <= bottom:
            break

        c.drawString(
            margin,
            y,
            line
        )

        y -= line_height


    # --------------------------------------------------------
    # Количество
    # --------------------------------------------------------

    qty = product_info.get(
        "qty",
        "?"
    )


    c.setFont(
        font_name,
        22
    )


    c.drawString(
        margin,
        14,
        f"КОЛ-ВО: {qty}"
    )


    c.save()

    packet.seek(0)


    return PdfReader(
        packet
    ).pages[0]


# ============================================================
# UI
# ============================================================

col1, col2 = st.columns(2)


with col1:

    labels_file = st.file_uploader(
        "1️⃣ Этикетки Ozon",
        type=["pdf"]
    )


with col2:

    assembly_file = st.file_uploader(
        "2️⃣ Лист подбора Ozon",
        type=["pdf"]
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


        with st.status(
            "Разбираем лист подбора..."
        ) as status:


            # ==================================================
            # ЛИСТ ПОДБОРА
            # ==================================================

            assembly_bytes = (
                assembly_file.getvalue()
            )


            assembly_data = parse_assembly_list(
                assembly_bytes
            )


            if not assembly_data:

                status.update(
                    label="Не удалось распознать лист подбора",
                    state="error"
                )

                st.stop()


            # ==================================================
            # ЭТИКЕТКИ
            # ==================================================

            status.update(
                label="Разбираем этикетки..."
            )


            labels_bytes = (
                labels_file.getvalue()
            )


            reader = PdfReader(
                BytesIO(
                    labels_bytes
                )
            )


            writer = PdfWriter()


            found = 0
            not_found = 0
            unknown = 0


            # ==================================================
            # КАЖДАЯ ЭТИКЕТКА
            # ==================================================

            for page in reader.pages:


                # ------------------------------------------------
                # Оригинальная страница
                # ------------------------------------------------

                writer.add_page(
                    page
                )


                # ------------------------------------------------
                # Номер отправления
                # ------------------------------------------------

                shipment = (
                    extract_shipment_from_label(
                        page
                    )
                )


                width = float(
                    page.mediabox.width
                )


                height = float(
                    page.mediabox.height
                )


                # =================================================
                # Совпадение
                # =================================================

                if shipment:

                    info = assembly_data.get(
                        shipment
                    )


                    if info:

                        found += 1

                        display_number = (
                            shipment.upper()
                        )


                    else:

                        not_found += 1

                        display_number = (
                            shipment.upper()
                        )


                        info = {

                            "label": "-",

                            "article": "-",

                            "qty": "?",

                            "name":
                                "ОТПРАВЛЕНИЕ НЕ НАЙДЕНО "
                                "В ЛИСТЕ ПОДБОРА"
                        }


                # =================================================
                # Номер не найден
                # =================================================

                else:

                    unknown += 1

                    display_number = "???"


                    info = {

                        "label": "-",

                        "article": "-",

                        "qty": "-",

                        "name":
                            "НОМЕР ОТПРАВЛЕНИЯ "
                            "НЕ РАСПОЗНАН"
                    }


                # =================================================
                # Добавляем информационный лист
                # =================================================

                writer.add_page(
                    create_info_label(
                        width,
                        height,
                        display_number,
                        info
                    )
                )


            # ==================================================
            # ГОТОВО
            # ==================================================

            status.update(
                label=(
                    f"Готово. "
                    f"Совпало: {found}; "
                    f"не найдено: {not_found}; "
                    f"не распознано: {unknown}"
                ),
                state="complete"
            )


        # ======================================================
        # PDF
        # ======================================================

        output = BytesIO()


        writer.write(
            output
        )


        output.seek(0)


        st.download_button(
            "📥 Скачать Ready_Labels.pdf",
            output,
            "Ready_Labels.pdf",
            "application/pdf",
            use_container_width=True
        )
