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
# НАСТРОЙКИ STREAMLIT
# ============================================================

st.set_page_config(
    page_title="Умная склейка этикеток Ozon",
    page_icon="🖨️",
    layout="wide"
)

st.title("🖨️ Склейка: Этикетки + Лист подбора")
st.write(
    "Сопоставление FBS Ozon по номеру отправления. "
    "Лист подбора разбирается по горизонтальным линиям."
)


# ============================================================
# ЗАГРУЗКА ШРИФТА
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

        with open(font_path, "wb") as file:
            file.write(response.content)

    pdfmetrics.registerFont(
        TTFont(
            "OzonFont",
            font_path
        )
    )

    return "OzonFont"


font_name = load_font()


# ============================================================
# НОРМАЛИЗАЦИЯ НОМЕРА ОТПРАВЛЕНИЯ
# ============================================================

def normalize_shipment(value):

    if not value:
        return ""

    value = str(value)

    # Иногда PDF вставляет пробелы
    value = re.sub(
        r"\s+",
        "",
        value
    )

    # Кириллическую І приводим к латинской I
    value = (
        value
        .replace("і", "i")
        .replace("І", "i")
    )

    return value.lower().strip()


# ============================================================
# НОРМАЛИЗАЦИЯ НОМЕРА ЭТИКЕТКИ
# ============================================================

def normalize_label(value):

    if not value:
        return ""

    digits = re.sub(
        r"\D",
        "",
        str(value)
    )

    if len(digits) >= 4:
        return digits[-4:]

    return digits


# ============================================================
# СОБИРАЕМ ТЕКСТ ИЗ WORDS
# ============================================================

def words_to_text(words):

    if not words:
        return ""

    words = sorted(
        words,
        key=lambda word: (
            round(float(word["top"]), 1),
            float(word["x0"])
        )
    )

    result = []

    current_y = None
    current_line = []

    for word in words:

        y = round(
            float(word["top"]),
            1
        )

        if (
            current_y is None
            or abs(y - current_y) <= 3
        ):

            current_line.append(
                word["text"]
            )

        else:

            if current_line:
                result.append(
                    " ".join(current_line)
                )

            current_line = [
                word["text"]
            ]

        current_y = y

    if current_line:
        result.append(
            " ".join(current_line)
        )

    return re.sub(
        r"\s+",
        " ",
        " ".join(result)
    ).strip()


# ============================================================
# ГОРИЗОНТАЛЬНЫЕ ЛИНИИ
# ============================================================

def get_horizontal_lines(page):

    lines = []

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

        # Линия должна быть горизонтальной
        if abs(y0 - y1) > 0.5:
            continue

        width = abs(x1 - x0)

        # Берем только длинные линии таблицы
        if width < page.width * 0.70:
            continue

        # pdfplumber top-coordinate
        top = page.height - y0

        lines.append(top)

    # Убираем дубликаты
    lines = sorted(lines)

    result = []

    for value in lines:

        if not result:
            result.append(value)
            continue

        if abs(value - result[-1]) > 1:
            result.append(value)

    return result


# ============================================================
# ПОИСК НОМЕРА ЭТИКЕТКИ
# ============================================================

def find_label_at_end(tokens):

    """
    Ищем 4-значный номер этикетки справа налево.

    Важно:
        8886 -> label

    Если PDF склеил:
        1 8886
    в:
        18886

    тогда:
        18886 -> qty=1, label=8886
    """

    for index in range(
        len(tokens) - 1,
        -1,
        -1
    ):

        token = tokens[index].strip()

        # Обычный вариант
        if re.fullmatch(
            r"\d{4}",
            token
        ):

            return {
                "label_index": index,
                "label": token,
                "qty_index": index - 1
            }

        # Склеенный вариант
        # Например 18886
        if re.fullmatch(
            r"\d{5,8}",
            token
        ):

            if len(token) > 4:

                possible_label = token[-4:]
                possible_qty = token[:-4]

                # Количество должно быть разумным
                if re.fullmatch(
                    r"\d{1,4}",
                    possible_qty
                ):

                    return {
                        "label_index": index,
                        "label": possible_label,
                        "qty_index": index,
                        "embedded_qty": possible_qty
                    }

    return None


# ============================================================
# РАЗБОР ХВОСТА СТРОКИ
# ============================================================

def parse_product_tail(text):

    """
    Разбирает:

        MW011401-4 1 8886

    в:

        article = MW011401-4
        qty     = 1
        label   = 8886
    """

    tokens = text.split()

    if len(tokens) < 3:
        return None


    # ========================================================
    # Ищем номер этикетки
    # ========================================================

    label_info = find_label_at_end(
        tokens
    )

    if not label_info:
        return None


    label_index = label_info[
        "label_index"
    ]

    label = label_info[
        "label"
    ]


    # ========================================================
    # Случай склеивания:
    #
    # MW011401-4 18886
    #
    #                 1 + 8886
    # ========================================================

    if "embedded_qty" in label_info:

        qty = label_info[
            "embedded_qty"
        ]

        article_index = (
            label_index - 1
        )

    else:

        qty_index = label_info[
            "qty_index"
        ]

        if qty_index < 0:
            return None

        qty = tokens[
            qty_index
        ]

        # Количество должно быть только числом
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


    # ========================================================
    # Артикул
    # ========================================================

    article = tokens[
        article_index
    ]


    # Если PDF дал перед количеством порядковый номер,
    # пробуем взять предыдущий токен
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


    # ========================================================
    # Название = всё до артикула
    # ========================================================

    name = " ".join(
        tokens[:article_index]
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
        "label": normalize_label(label),
        "name": name
    }


# ============================================================
# ПАРСЕР ОДНОЙ СТРОКИ
# ============================================================

def parse_assembly_row(row_words):

    if not row_words:
        return None


    # ========================================================
    # Общий текст строки
    # ========================================================

    row_text = words_to_text(
        row_words
    )


    if not row_text:
        return None


    # ========================================================
    # Ищем номер отправления
    # ========================================================

    shipment_matches = re.findall(
        r"\d{8,15}-\d{4}-\d+",
        row_text
    )


    if not shipment_matches:
        return None


    shipment = normalize_shipment(
        shipment_matches[0]
    )


    # ========================================================
    # Убираем номер отправления
    # ========================================================

    work_text = row_text

    for order in shipment_matches:

        work_text = work_text.replace(
            order,
            " "
        )


    # ========================================================
    # Убираем ii500...
    #
    # Это номер с этикетки, а не отправление.
    # ========================================================

    work_text = re.sub(
        r"\bii\d{8,20}\b",
        " ",
        work_text,
        flags=re.IGNORECASE
    )


    # Возможная кириллическая І
    work_text = re.sub(
        r"\b[іi][iі]\d{8,20}\b",
        " ",
        work_text,
        flags=re.IGNORECASE
    )


    # ========================================================
    # Убираем служебные заголовки
    # ========================================================

    work_text = re.sub(
        r"\b(?:Фото|Товар|Артикул|Кол-во|Этикетка)\b",
        " ",
        work_text,
        flags=re.IGNORECASE
    )


    work_text = re.sub(
        r"\s+",
        " ",
        work_text
    ).strip()


    if not work_text:
        return None


    # ========================================================
    # Разбираем хвост:
    #
    # Артикул → Количество → Этикетка
    # ========================================================

    product = parse_product_tail(
        work_text
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
# ПАРСИНГ ЛИСТА ПОДБОРА
# ============================================================

@st.cache_data(show_spinner=False)
def parse_assembly_list(pdf_bytes):

    data = {}

    debug_rows = []


    with pdfplumber.open(
        BytesIO(pdf_bytes)
    ) as pdf:

        for page_number, page in enumerate(
            pdf.pages,
            start=1
        ):

            # ==================================================
            # Получаем горизонтальные линии
            # ==================================================

            lines = get_horizontal_lines(
                page
            )


            if len(lines) < 2:
                continue


            # ==================================================
            # Получаем слова страницы
            # ==================================================

            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False
            )


            if not words:
                continue


            # ==================================================
            # Границы строк
            # ==================================================

            boundaries = []

            # Добавляем верхнюю границу страницы
            boundaries.append(0)


            for line_y in lines:

                if (
                    not boundaries
                    or
                    abs(
                        line_y -
                        boundaries[-1]
                    ) > 1
                ):

                    boundaries.append(
                        line_y
                    )


            # Добавляем нижнюю границу
            boundaries.append(
                page.height
            )


            # ==================================================
            # Обрабатываем каждую секцию
            # ==================================================

            for i in range(
                len(boundaries) - 1
            ):

                top = boundaries[i]
                bottom = boundaries[i + 1]


                # Слишком маленькая область
                if bottom - top < 15:
                    continue


                # =================================================
                # Берем слова только внутри горизонтальных линий
                # =================================================

                row_words = []

                for word in words:

                    word_top = float(
                        word["top"]
                    )

                    word_bottom = float(
                        word["bottom"]
                    )


                    if (
                        word_top >= top + 1
                        and
                        word_bottom <= bottom + 1
                    ):

                        row_words.append(
                            word
                        )


                if not row_words:
                    continue


                # =================================================
                # Разбираем строку
                # =================================================

                row = parse_assembly_row(
                    row_words
                )


                if not row:
                    continue


                shipment = row[
                    "shipment"
                ]


                # =================================================
                # Если один номер уже был,
                # не перезаписываем случайно
                # =================================================

                if shipment in data:

                    # Если найденная запись полностью такая же,
                    # ничего не делаем.
                    if data[shipment] == row:
                        continue


                data[shipment] = row


                debug_rows.append({

                    "Страница": page_number,

                    "Номер отправления":
                        shipment,

                    "Номер с этикетки":
                        row["label"],

                    "Артикул":
                        row["article"],

                    "Количество":
                        row["qty"],

                    "Товар":
                        row["name"]
                })


    # ============================================================
    # Показываем диагностику
    # ============================================================

    if data:

        st.success(
            f"Найдено отправлений: {len(data)}"
        )

        with st.expander(
            "🔗 Маппинг листа подбора",
            expanded=True
        ):

            st.dataframe(
                debug_rows,
                use_container_width=True,
                hide_index=True
            )

    else:

        st.error(
            "Не удалось прочитать лист подбора."
        )


    return data


# ============================================================
# ИЗВЛЕЧЕНИЕ НОМЕРА С ЭТИКЕТКИ
# ============================================================

def extract_shipment_from_label(page):

    text = page.extract_text()

    if not text:
        return None


    # ========================================================
    # Сначала обычный FBS номер
    # ========================================================

    match = re.search(
        r"\d{8,15}-\d{4}-\d+",
        text
    )


    if match:

        return normalize_shipment(
            match.group(0)
        )


    # ========================================================
    # Затем ii...
    # ========================================================

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


    # ========================================================
    # Номер заказа
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
    # Номер этикетки
    # ========================================================

    label = product_info.get(
        "label",
        "-"
    )


    c.setFont(
        font_name,
        9
    )

    c.drawString(
        x_margin,
        height - 32,
        f"Этикетка: {label}"
    )


    # ========================================================
    # Артикул
    # ========================================================

    c.setFont(
        font_name,
        12
    )

    article = product_info.get(
        "article",
        "-"
    )


    if len(article) > 25:

        article = (
            article[:22]
            + "..."
        )


    c.drawString(
        x_margin,
        height - 48,
        f"Арт: {article}"
    )


    # ========================================================
    # Название
    # ========================================================

    name = product_info.get(
        "name",
        "Товар не найден"
    )


    top_limit = height - 65
    bottom_limit = 50

    available_h = (
        top_limit -
        bottom_limit
    )


    current_size = 10
    line_h = 12


    def get_lines(
        text,
        chars
    ):

        words = text.split()

        result = []

        current = ""


        for word in words:

            candidate = (
                current +
                word +
                " "
            )


            if len(candidate) <= chars:

                current = candidate

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


    lines = get_lines(
        name,
        32
    )


    while (
        len(lines) * line_h
        > available_h
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
                    10 /
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


    # ========================================================
    # Количество
    # ========================================================

    qty = product_info.get(
        "qty",
        "?"
    )


    c.setFont(
        font_name,
        24
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

col1, col2 = st.columns(2)


with col1:

    labels_file = st.file_uploader(
        "1️⃣ Этикетки (PDF)",
        type=["pdf"]
    )


with col2:

    assembly_file = st.file_uploader(
        "2️⃣ Лист подбора отправлений (PDF)",
        type=["pdf"]
    )


# ============================================================
# КНОПКА СКЛЕЙКИ
# ============================================================

if labels_file and assembly_file:

    if st.button(
        "🚀 Склеить файлы",
        type="primary",
        use_container_width=True
    ):

        with st.status(
            "Анализируем лист подбора..."
        ) as status:

            # ==================================================
            # Читаем PDF листа подбора
            # ==================================================

            assembly_bytes = (
                assembly_file.getvalue()
            )


            assembly_data = parse_assembly_list(
                assembly_bytes
            )


            if not assembly_data:

                status.update(
                    label="Лист подбора не распознан",
                    state="error"
                )

                st.stop()


            # ==================================================
            # Читаем этикетки
            # ==================================================

            status.update(
                label="Читаем этикетки..."
            )


            labels_bytes = (
                labels_file.getvalue()
            )


            reader = PdfReader(
                BytesIO(labels_bytes)
            )


            writer = PdfWriter()


            found_count = 0
            not_found_count = 0
            unknown_count = 0


            # ==================================================
            # Обрабатываем страницы этикеток
            # ==================================================

            for i, page in enumerate(
                reader.pages
            ):

                # ------------------------------------------------
                # Оригинальная этикетка
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


                # ------------------------------------------------
                # Размер страницы
                # ------------------------------------------------

                width = float(
                    page.mediabox.width
                )

                height = float(
                    page.mediabox.height
                )


                # =================================================
                # Отправление найдено
                # =================================================

                if shipment:

                    info = assembly_data.get(
                        shipment
                    )


                    # ---------------------------------------------
                    # Совпадение
                    # ---------------------------------------------

                    if info:

                        found_count += 1

                        display_number = (
                            shipment.upper()
                        )


                        if display_number.startswith(
                            "II"
                        ):

                            display_number = (
                                "ii"
                                +
                                display_number[2:]
                            )


                    # ---------------------------------------------
                    # Нет в листе подбора
                    # ---------------------------------------------

                    else:

                        not_found_count += 1

                        display_number = (
                            shipment.upper()
                        )


                        info = {

                            "shipment": shipment,

                            "label": "-",

                            "name":
                                "ОТПРАВЛЕНИЕ НЕ НАЙДЕНО "
                                "В ЛИСТЕ ПОДБОРА",

                            "article": "-",

                            "qty": "?"
                        }


                # =================================================
                # Номер не распознан
                # =================================================

                else:

                    unknown_count += 1

                    display_number = "???"


                    info = {

                        "shipment": "",

                        "label": "-",

                        "name":
                            "НОМЕР ОТПРАВЛЕНИЯ "
                            "НЕ РАСПОЗНАН",

                        "article": "-",

                        "qty": "-"
                    }


                # =================================================
                # Добавляем информационную страницу
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
            # Завершение
            # ==================================================

            status.update(
                label=(
                    f"Готово! "
                    f"Совпало: {found_count}; "
                    f"не найдено: {not_found_count}; "
                    f"не распознано: {unknown_count}"
                ),
                state="complete"
            )


        # ======================================================
        # Создаем итоговый PDF
        # ======================================================

        output = BytesIO()

        writer.write(
            output
        )

        output.seek(0)


        # ======================================================
        # Скачать
        # ======================================================

        st.download_button(
            "📥 Скачать результат",
            output,
            "Ready_Labels.pdf",
            "application/pdf",
            "application/pdf",
            use_container_width=True
        )
