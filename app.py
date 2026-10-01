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

st.title(
    "🖨️ Ozon FBS — Этикетки + Лист подбора"
)

st.write(
    "Маппинг: номер отправления → номер с этикетки → "
    "товар → артикул → количество"
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

        with open(
            font_path,
            "wb"
        ) as f:

            f.write(
                response.content
            )

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
# НОРМАЛИЗАЦИЯ НОМЕРА ОТПРАВЛЕНИЯ
# ============================================================

def normalize_shipment(value):

    if not value:

        return ""

    value = str(
        value
    )

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
# ГОРИЗОНТАЛЬНЫЕ ЛИНИИ
# ============================================================

def get_horizontal_lines(page):

    result = []

    page_width = float(
        page.width
    )


    # ========================================================
    # 1. Обычные линии PDF
    # ========================================================

    for line in page.lines:

        x0 = float(
            line.get(
                "x0",
                0
            )
        )

        x1 = float(
            line.get(
                "x1",
                0
            )
        )

        y0 = float(
            line.get(
                "y0",
                0
            )
        )

        y1 = float(
            line.get(
                "y1",
                0
            )
        )


        # Только горизонтальные
        if abs(
            y0 - y1
        ) > 1:

            continue


        width = abs(
            x1 - x0
        )


        # Ozon может использовать
        # относительно короткие разделители
        if width < page_width * 0.30:

            continue


        # pdfplumber может уже дать top
        if "top" in line:

            top = float(
                line["top"]
            )

        else:

            top = (
                float(page.height)
                -
                y0
            )


        result.append(
            top
        )


    # ========================================================
    # 2. Разделители как rect
    # ========================================================

    for rect in page.rects:

        x0 = float(
            rect.get(
                "x0",
                0
            )
        )

        x1 = float(
            rect.get(
                "x1",
                0
            )
        )

        y0 = float(
            rect.get(
                "y0",
                0
            )
        )

        y1 = float(
            rect.get(
                "y1",
                0
            )
        )


        width = abs(
            x1 - x0
        )

        height = abs(
            y1 - y0
        )


        if (
            height <= 2
            and
            width >= page_width * 0.30
        ):

            if "top" in rect:

                top = float(
                    rect["top"]
                )

            else:

                top = (
                    float(page.height)
                    -
                    y0
                )


            result.append(
                top
            )


    # ========================================================
    # 3. Убираем дубликаты
    # ========================================================

    result.sort()

    unique = []


    for y in result:

        if not unique:

            unique.append(
                y
            )

        elif abs(
            y - unique[-1]
        ) > 2:

            unique.append(
                y
            )


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


    # ========================================================
    # Берём слово по центру
    # ========================================================

    for word in words:

        word_top = float(
            word["top"]
        )

        word_bottom = float(
            word["bottom"]
        )


        word_center = (
            word_top
            +
            word_bottom
        ) / 2


        if (
            word_center > top + 2
            and
            word_center < bottom - 2
        ):

            row_words.append(
                word
            )


    if not row_words:

        return ""


    # ========================================================
    # Сортировка
    # ========================================================

    row_words.sort(
        key=lambda w: (
            float(w["top"]),
            float(w["x0"])
        )
    )


    lines = []

    current_line = []

    current_y = None


    # ========================================================
    # Восстанавливаем строки
    # ========================================================

    for word in row_words:

        y = float(
            word["top"]
        )


        if (
            current_y is None
            or
            abs(
                y - current_y
            ) <= 3
        ):

            current_line.append(
                word
            )

        else:

            current_line.sort(
                key=lambda w:
                float(w["x0"])
            )


            lines.append(
                " ".join(
                    w["text"]
                    for w in current_line
                )
            )


            current_line = [
                word
            ]


        current_y = y


    # ========================================================
    # Последняя строка
    # ========================================================

    if current_line:

        current_line.sort(
            key=lambda w:
            float(w["x0"])
        )


        lines.append(
            " ".join(
                w["text"]
                for w in current_line
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


    # Фото
    text = re.sub(
        r"\bФото\b",
        " ",
        text,
        flags=re.IGNORECASE
    )


    # Товар
    text = re.sub(
        r"\bТовар\b",
        " ",
        text,
        flags=re.IGNORECASE
    )


    # Артикул
    text = re.sub(
        r"\bАртикул\b",
        " ",
        text,
        flags=re.IGNORECASE
    )


    # Кол-во
    text = re.sub(
        r"\bКол-во\b",
        " ",
        text,
        flags=re.IGNORECASE
    )


    # Этикетка
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
# ПАРСИНГ:
#
# НАЗВАНИЕ → АРТИКУЛ → КОЛИЧЕСТВО → ЭТИКЕТКА
# ============================================================

def parse_product_tail(text):

    if not text:

        return None


    tokens = text.split()


    if len(tokens) < 3:

        return None


    # ========================================================
    # Ищем 4-значный номер этикетки справа налево
    # ========================================================

    label_index = None

    label = None


    for i in range(
        len(tokens) - 1,
        -1,
        -1
    ):

        token = tokens[
            i
        ].strip()


        if re.fullmatch(
            r"\d{4}",
            token
        ):

            label_index = i

            label = token

            break


    # ========================================================
    # Если 4 цифры не найдены отдельно,
    # пробуем склеенный вариант.
    #
    # Например:
    #
    # 18886
    #
    # где:
    # 1 = количество
    # 8886 = этикетка
    # ========================================================

    if label_index is None:

        for i in range(
            len(tokens) - 1,
            -1,
            -1
        ):

            token = tokens[
                i
            ].strip()


            if not re.fullmatch(
                r"\d{5,8}",
                token
            ):

                continue


            possible_label = token[
                -4:
            ]


            possible_qty = token[
                :-4
            ]


            if re.fullmatch(
                r"\d{1,4}",
                possible_qty
            ):

                label_index = i

                label = possible_label

                qty = possible_qty

                article_index = (
                    i - 1
                )

                break

        else:

            return None


    # ========================================================
    # Обычный вариант
    # ========================================================

    if "qty" not in locals():

        qty_index = (
            label_index - 1
        )


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


    # ========================================================
    # Иногда перед артикулом есть № строки
    # ========================================================

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
    # Название товара
    # ========================================================

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

        "article":
            article,

        "qty":
            qty,

        "label":
            label,

        "name":
            name
    }


# ============================================================
# ПАРСИНГ ОДНОЙ ПОЗИЦИИ
# ============================================================

def parse_row(text):

    if not text:

        return None


    # ========================================================
    # Номер отправления
    # ========================================================

    shipment_matches = re.findall(
        r"\d{8,15}-\d{4}-\d+",
        text
    )


    if not shipment_matches:

        return None


    shipment = normalize_shipment(
        shipment_matches[0]
    )


    # ========================================================
    # Убираем номер отправления
    # ========================================================

    work = text


    for shipment_number in shipment_matches:

        work = work.replace(
            shipment_number,
            " "
        )


    # ========================================================
    # Убираем ii...
    #
    # Это номер с этикетки.
    # ========================================================

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


    # ========================================================
    # Разбираем хвост
    # ========================================================

    product = parse_product_tail(
        work
    )


    if not product:

        return None


    return {

        "shipment":
            shipment,

        "label":
            product["label"],

        "name":
            product["name"],

        "article":
            product["article"],

        "qty":
            product["qty"]
    }


# ============================================================
# РЕЗЕРВНЫЙ ПАРСЕР
#
# Если линии PDF есть, но текст между линиями не работает,
# ищем номера отправлений напрямую.
# ============================================================

def parse_assembly_by_shipments(
    page,
    words
):

    results = []


    if not words:

        return results


    # ========================================================
    # Ищем номера отправлений среди слов
    # ========================================================

    shipment_items = []


    for word in words:

        text = str(
            word.get(
                "text",
                ""
            )
        ).strip()


        if not text:

            continue


        matches = re.findall(
            r"\d{8,15}-\d{4}-\d+",
            text
        )


        for match in matches:

            shipment_items.append({

                "shipment":
                    normalize_shipment(
                        match
                    ),

                "top":
                    float(
                        word["top"]
                    ),

                "bottom":
                    float(
                        word["bottom"]
                    ),

                "x0":
                    float(
                        word["x0"]
                    )
            })


    # ========================================================
    # Если номер разбит PDF на несколько слов
    # ========================================================

    if not shipment_items:

        page_text = " ".join(
            str(
                word.get(
                    "text",
                    ""
                )
            )
            for word in words
        )


        page_text_clean = re.sub(
            r"\s*-\s*",
            "-",
            page_text
        )


        matches = re.findall(
            r"\d{8,15}-\d{4}-\d+",
            page_text_clean
        )


        for match in matches:

            normalized = normalize_shipment(
                match
            )


            first_part = (
                match.split("-")[0]
            )


            for word in words:

                word_text = str(
                    word.get(
                        "text",
                        ""
                    )
                ).strip()


                if first_part in word_text:

                    shipment_items.append({

                        "shipment":
                            normalized,

                        "top":
                            float(
                                word["top"]
                            ),

                        "bottom":
                            float(
                                word["bottom"]
                            ),

                        "x0":
                            float(
                                word["x0"]
                            )

                    })


                    break


    # ========================================================
    # Ничего не нашли
    # ========================================================

    if not shipment_items:

        return results


    # ========================================================
    # Сортировка
    # ========================================================

    shipment_items.sort(
        key=lambda x: (
            x["top"],
            x["x0"]
        )
    )


    # ========================================================
    # Убираем дубли
    # ========================================================

    unique_shipments = []

    seen = set()


    for item in shipment_items:

        key = (
            item["shipment"],
            round(
                item["top"],
                1
            )
        )


        if key in seen:

            continue


        seen.add(
            key
        )


        unique_shipments.append(
            item
        )


    shipment_items = (
        unique_shipments
    )


    # ========================================================
    # Каждая позиция:
    #
    # текущий номер
    #
    # →
    #
    # следующий номер
    # ========================================================

    page_height = float(
        page.height
    )


    for index, item in enumerate(
        shipment_items
    ):

        shipment_top = item[
            "top"
        ]


        if (
            index + 1
            <
            len(shipment_items)
        ):

            next_top = shipment_items[
                index + 1
            ]["top"]


            bottom = (
                next_top - 1
            )

        else:

            bottom = page_height


        if bottom <= shipment_top:

            continue


        row_words = []


        # ====================================================
        # Берём слова внутри области
        # ====================================================

        for word in words:

            word_top = float(
                word["top"]
            )

            word_bottom = float(
                word["bottom"]
            )


            word_center = (
                word_top
                +
                word_bottom
            ) / 2


            if (
                word_center
                >=
                shipment_top - 2
                and
                word_center
                <
                bottom
            ):

                row_words.append(
                    word
                )


        if not row_words:

            continue


        # ====================================================
        # Сортировка слов
        # ====================================================

        row_words.sort(
            key=lambda w: (
                round(
                    float(
                        w["top"]
                    ),
                    1
                ),
                float(
                    w["x0"]
                )
            )
        )


        text_lines = []

        current_line = []

        current_y = None


        # ====================================================
        # Восстанавливаем текстовые строки
        # ====================================================

        for word in row_words:

            y = float(
                word["top"]
            )


            if (
                current_y is None
                or
                abs(
                    y - current_y
                ) <= 3
            ):

                current_line.append(
                    word
                )

            else:

                current_line.sort(
                    key=lambda w:
                    float(
                        w["x0"]
                    )
                )


                text_lines.append(
                    " ".join(
                        w["text"]
                        for w in current_line
                    )
                )


                current_line = [
                    word
                ]


            current_y = y


        if current_line:

            current_line.sort(
                key=lambda w:
                float(
                    w["x0"]
                )
            )


            text_lines.append(
                " ".join(
                    w["text"]
                    for w in current_line
                )
            )


        row_text = "\n".join(
            text_lines
        )


        if not row_text:

            continue


        # ====================================================
        # Парсим обычным парсером
        # ====================================================

        parsed = parse_row(
            row_text
        )


        if parsed:

            results.append(
                parsed
            )


    return results


# ============================================================
# ОСНОВНОЙ ПАРСЕР ЛИСТА ПОДБОРА
# ============================================================

@st.cache_data(show_spinner=False)
def parse_assembly_list(pdf_bytes):

    data = {}

    diagnostics = []


    with pdfplumber.open(
        BytesIO(
            pdf_bytes
        )
    ) as pdf:


        total_pages = len(
            pdf.pages
        )


        # ====================================================
        # ВСЕ СТРАНИЦЫ
        # ====================================================

        for page_number, page in enumerate(
            pdf.pages,
            start=1
        ):


            # ==================================================
            # ЛИНИИ
            # ==================================================

            lines = get_horizontal_lines(
                page
            )


            # ==================================================
            # СЛОВА
            # ==================================================

            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False
            )


            if not words:

                diagnostics.append({

                    "Страница":
                        page_number,

                    "Горизонтальных линий":
                        len(lines),

                    "Сформировано строк":
                        0,

                    "Найдено отправлений":
                        0,

                    "Режим":
                        "Нет текста"
                })

                continue


            page_rows = 0

            page_found = 0

            fallback_used = False


            # ==================================================
            # ПЕРВЫЙ РЕЖИМ
            #
            # По горизонтальным линиям
            # ==================================================

            if len(lines) >= 2:

                boundaries = [
                    0
                ]


                boundaries.extend(
                    lines
                )


                boundaries.append(
                    float(page.height)
                )


                boundaries = sorted(
                    set(
                        round(
                            x,
                            2
                        )
                        for x in boundaries
                    )
                )


                for i in range(
                    len(boundaries) - 1
                ):

                    top = boundaries[
                        i
                    ]

                    bottom = boundaries[
                        i + 1
                    ]


                    if (
                        bottom - top
                        < 8
                    ):

                        continue


                    row_text = (
                        get_text_between_lines(
                            page,
                            words,
                            top,
                            bottom
                        )
                    )


                    if not row_text:

                        continue


                    page_rows += 1


                    result = parse_row(
                        row_text
                    )


                    if not result:

                        continue


                    shipment = result[
                        "shipment"
                    ]


                    if shipment not in data:

                        data[
                            shipment
                        ] = result

                        page_found += 1


            # ==================================================
            # ВТОРОЙ РЕЖИМ
            #
            # Если по линиям ничего не найдено,
            # ищем отправления напрямую.
            # ==================================================

            if page_found == 0:

                fallback_used = True


                fallback_results = (
                    parse_assembly_by_shipments(
                        page,
                        words
                    )
                )


                for result in fallback_results:

                    shipment = result[
                        "shipment"
                    ]


                    if shipment not in data:

                        data[
                            shipment
                        ] = result

                        page_found += 1


                if fallback_results:

                    page_rows = max(
                        page_rows,
                        len(
                            fallback_results
                        )
                    )


            # ==================================================
            # ДИАГНОСТИКА
            # ==================================================

            diagnostics.append({

                "Страница":
                    page_number,

                "Горизонтальных линий":
                    len(lines),

                "Сформировано строк":
                    page_rows,

                "Найдено отправлений":
                    page_found,

                "Режим":
                    (
                        "Резервный"
                        if fallback_used
                        else
                        "По линиям"
                    )
            })


    # ============================================================
    # ДИАГНОСТИКА
    # ============================================================

    st.subheader(
        "📊 Диагностика листа подбора"
    )


    col1, col2, col3, col4 = st.columns(4)


    with col1:

        st.metric(
            "Страниц",
            total_pages
        )


    with col2:

        st.metric(
            "Найдено отправлений",
            len(data)
        )


    with col3:

        st.metric(
            "Ожидается",
            236
        )


    with col4:

        difference = (
            len(data)
            -
            236
        )


        st.metric(
            "Разница",
            difference
        )


    # ============================================================
    # ДИАГНОСТИКА ПО СТРАНИЦАМ
    # ============================================================

    with st.expander(
        "🔍 Диагностика страниц",
        expanded=True
    ):

        st.dataframe(
            diagnostics,
            use_container_width=True,
            hide_index=True
        )


    # ============================================================
    # МАППИНГ
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
            "Не найдено ни одного номера отправления. "
            "Проверьте PDF листа подбора."
        )


    return data


# ============================================================
# НОМЕР ОТПРАВЛЕНИЯ НА ЭТИКЕТКЕ
# ============================================================

def extract_shipment_from_label(page):

    text = page.extract_text()


    if not text:

        return None


    # ========================================================
    # Основной формат
    #
    # 78277691-0407-1
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
    # ii...
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


    # ========================================================
    # Номер заказа
    # ========================================================

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


    # ========================================================
    # Номер с этикетки
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
        margin,
        height - 32,
        f"Этикетка: {label}"
    )


    # ========================================================
    # Артикул
    # ========================================================

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


    # ========================================================
    # Название
    # ========================================================

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
                current
                +
                word
                +
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
                    word
                    +
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
        len(lines)
        *
        line_height
        >
        top - bottom
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
                    30
                    *
                    9
                    /
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


    # ========================================================
    # Количество
    # ========================================================

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


            assembly_data = (
                parse_assembly_list(
                    assembly_bytes
                )
            )


            if not assembly_data:

                status.update(
                    label=(
                        "Не удалось распознать "
                        "лист подбора"
                    ),
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

                    info = (
                        assembly_data.get(
                            shipment
                        )
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

                            "label":
                                "-",

                            "article":
                                "-",

                            "qty":
                                "?",

                            "name":
                                (
                                    "ОТПРАВЛЕНИЕ "
                                    "НЕ НАЙДЕНО "
                                    "В ЛИСТЕ ПОДБОРА"
                                )
                        }


                # =================================================
                # Номер не найден
                # =================================================

                else:

                    unknown += 1

                    display_number = (
                        "???"
                    )


                    info = {

                        "label":
                            "-",

                        "article":
                            "-",

                        "qty":
                            "-",

                        "name":
                            (
                                "НОМЕР ОТПРАВЛЕНИЯ "
                                "НЕ РАСПОЗНАН"
                            )
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
        # СОЗДАЁМ PDF
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
