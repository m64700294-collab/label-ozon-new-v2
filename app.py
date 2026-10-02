import streamlit as st
import re
import os
import csv
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# НАСТРОЙКИ
# ============================================================

st.set_page_config(
    page_title="Ozon — Этап 2 — Лист подбора",
    page_icon="🔎",
    layout="wide"
)

st.title("🔎 Ozon — Этап 2 — Связка этикеток с листом подбора")

st.write(
    """
    Этот этап обрабатывает только этикетки, которые на первом этапе
    получили статус «НЕ РАСПОЗНАН».

    Связка выполняется через PDF листа подбора:

    `87180955-0554-25` → `II5010320 2549` → `2549`

    После этого номер отправления ищется в выгрузке Ozon API,
    откуда берутся Артикул, SKU и Количество.
    """
)


# ============================================================
# ШРИФТ
# ============================================================

@st.cache_resource
def load_font():

    font_path = "Roboto_Full_Final.ttf"

    if not os.path.exists(font_path):

        st.error(
            "Файл Roboto_Full_Final.ttf не найден. "
            "Положите его рядом с app_etap2.py."
        )

        raise FileNotFoundError(
            "Roboto_Full_Final.ttf"
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

def normalize_order(value):

    if value is None:
        return ""

    value = str(value).strip()

    if not value:
        return ""

    value = (
        value
        .replace("\u200b", "")
        .replace("\xa0", " ")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )

    value = re.sub(
        r"\s*-\s*",
        "-",
        value
    )

    return value.lower()


def numeric_order_key(value):

    return re.sub(
        r"\D",
        "",
        normalize_order(value)
    )


# ============================================================
# НОРМАЛИЗАЦИЯ НОМЕРА ЭТИКЕТКИ
# ============================================================

def normalize_label_key(value):

    if value is None:
        return ""

    value = str(value).strip()

    value = (
        value
        .replace("\u200b", "")
        .replace("\xa0", " ")
    )

    # Только последние 4 цифры
    match = re.search(
        r"(\d{4})\s*$",
        value
    )

    if match:

        return match.group(1)

    # Если пришло просто число
    digits = re.sub(
        r"\D",
        "",
        value
    )

    if len(digits) >= 4:

        return digits[-4:]

    return ""


# ============================================================
# ПОИСК НОМЕРА ОТПРАВЛЕНИЯ
# ============================================================

ORDER_PATTERN = re.compile(
    r"\d{6,15}\s*-\s*\d{2,6}\s*-\s*\d+"
)


def find_orders(text):

    if not text:
        return []

    result = []

    for match in ORDER_PATTERN.findall(text):

        order = normalize_order(match)

        if order and order not in result:

            result.append(order)

    return result


# ============================================================
# ПОИСК 4-ЗНАЧНОГО НОМЕРА ЭТИКЕТКИ
#
# Поддерживаем:
#
# II5010320 2549
# II50103202549
# ll5010320 2549
# 115010320 2549
# ИI5010320 2549
# ============================================================

LABEL_PATTERNS = [

    # II5010320 2549
    re.compile(
        r"(?:ii|ll|1i|i1|ии|иi|iи)"
        r"\s*\d{6,12}"
        r"\s*[-\s]?"
        r"(\d{4})",
        re.IGNORECASE
    ),

    # II50103202549
    re.compile(
        r"(?:ii|ll|1i|i1|ии|иi|iи)"
        r"\s*\d{10,16}",
        re.IGNORECASE
    ),

    # Более свободный вариант:
    # длинный код + последние 4 цифры
    re.compile(
        r"\b\d{6,12}\s+(\d{4})\b"
    )
]


def find_label_keys(text):

    if not text:
        return []

    result = []

    for pattern in LABEL_PATTERNS:

        for match in pattern.finditer(text):

            value = ""

            if match.groups():

                value = match.group(1)

            else:

                value = match.group(0)

                digits = re.sub(
                    r"\D",
                    "",
                    value
                )

                if len(digits) >= 4:

                    value = digits[-4:]

            value = normalize_label_key(
                value
            )

            if (
                value
                and value not in result
            ):

                result.append(value)

    return result


# ============================================================
# PDF — ИЗВЛЕЧЕНИЕ СТРАНИЦ
# ============================================================

def extract_pdf_pages(pdf_file):

    pdf_file.seek(0)

    reader = PdfReader(
        pdf_file
    )

    pages = []

    for page_number, page in enumerate(
        reader.pages,
        start=1
    ):

        try:

            text = (
                page.extract_text()
                or ""
            )

        except Exception:

            text = ""

        pages.append(
            {
                "page": page_number,
                "text": text
            }
        )

    return pages


# ============================================================
# ПОПЫТКА ИЗВЛЕЧЬ ДАННЫЕ ЧЕРЕЗ PDFPLUMBER
#
# Здесь используем координаты, если библиотека установлена.
# Это важно для листа подбора.
# ============================================================

def extract_words_with_coordinates(
    pdf_file
):

    try:

        import pdfplumber

    except ImportError:

        return []

    pdf_file.seek(0)

    result = []

    with pdfplumber.open(
        pdf_file
    ) as pdf:

        for page_number, page in enumerate(
            pdf.pages,
            start=1
        ):

            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=3,
                keep_blank_chars=False
            )

            result.append(
                {
                    "page": page_number,
                    "width": page.width,
                    "height": page.height,
                    "words": words
                }
            )

    return result


# ============================================================
# ПОСТРОЕНИЕ СТРОК ИЗ WORDS
# ============================================================

def build_lines_from_words(words):

    if not words:

        return []

    sorted_words = sorted(
        words,
        key=lambda x: (
            round(
                float(x.get("top", 0)),
                1
            ),
            float(
                x.get("x0", 0)
            )
        )
    )

    lines = []

    for word in sorted_words:

        text = str(
            word.get(
                "text",
                ""
            )
        ).strip()

        if not text:
            continue

        top = float(
            word.get(
                "top",
                0
            )
        )

        # Ищем существующую строку
        found = None

        for line in lines:

            if abs(
                line["top"] - top
            ) <= 4:

                found = line

                break

        if found is None:

            found = {
                "top": top,
                "words": []
            }

            lines.append(
                found
            )

        found["words"].append(
            word
        )

    result = []

    for line in lines:

        line["words"].sort(
            key=lambda x:
                float(
                    x.get(
                        "x0",
                        0
                    )
                )
        )

        text = " ".join(
            str(
                w.get(
                    "text",
                    ""
                )
            )
            for w in line["words"]
        )

        result.append(
            {
                "top": line["top"],
                "text": text,
                "words": line["words"]
            }
        )

    result.sort(
        key=lambda x:
            x["top"]
    )

    return result


# ============================================================
# ПОИСК СВЯЗКИ:
#
# 87180955-0554-25
# II5010320 2549
#
# ============================================================

def find_picklist_mapping(
    pdf_file
):

    mappings = []

    coordinate_pages = (
        extract_words_with_coordinates(
            pdf_file
        )
    )

    # ========================================================
    # ВАРИАНТ 1 — PDFPLUMBER + КООРДИНАТЫ
    # ========================================================

    if coordinate_pages:

        for page_data in coordinate_pages:

            page_number = page_data[
                "page"
            ]

            words = page_data[
                "words"
            ]

            lines = build_lines_from_words(
                words
            )

            # ----------------------------------------------
            # Ищем строки
            # ----------------------------------------------

            for line_index, line in enumerate(
                lines
            ):

                line_text = line[
                    "text"
                ]

                orders = find_orders(
                    line_text
                )

                # Если номер заказа найден
                if orders:

                    order = orders[0]

                    # Ищем номер этикетки
                    # в этой же строке
                    # или ближайших строках

                    candidate_texts = [
                        line_text
                    ]

                    for offset in [
                        -2,
                        -1,
                        1,
                        2
                    ]:

                        index = (
                            line_index
                            + offset
                        )

                        if (
                            index >= 0
                            and index < len(lines)
                        ):

                            candidate_texts.append(
                                lines[index][
                                    "text"
                                ]
                            )

                    label_key = ""

                    for candidate_text in (
                        candidate_texts
                    ):

                        found_keys = (
                            find_label_keys(
                                candidate_text
                            )
                        )

                        if found_keys:

                            label_key = (
                                found_keys[0]
                            )

                            break

                    if label_key:

                        mappings.append(
                            {
                                "page": page_number,
                                "order": order,
                                "label_key": label_key,
                                "method": "координаты"
                            }
                        )

    # ========================================================
    # ВАРИАНТ 2 — ОБЫЧНЫЙ TEXT EXTRACTION
    #
    # Если pdfplumber не смог определить координаты.
    # ========================================================

    if not mappings:

        pages = extract_pdf_pages(
            pdf_file
        )

        for page_data in pages:

            text = page_data[
                "text"
            ]

            orders = find_orders(
                text
            )

            if not orders:
                continue

            order = orders[0]

            label_keys = find_label_keys(
                text
            )

            if not label_keys:
                continue

            mappings.append(
                {
                    "page": page_data["page"],
                    "order": order,
                    "label_key": label_keys[0],
                    "method": "текст PDF"
                }
            )

    # ========================================================
    # УДАЛЯЕМ ДУБЛИ
    # ========================================================

    unique = {}

    for item in mappings:

        key = (
            item["order"],
            item["label_key"]
        )

        unique[key] = item

    return list(
        unique.values()
    )


# ============================================================
# ЧТЕНИЕ XLSX
# ============================================================

def read_xlsx(file):

    import openpyxl

    file.seek(0)

    workbook = openpyxl.load_workbook(
        file,
        read_only=True,
        data_only=True
    )

    sheet = workbook[
        workbook.sheetnames[0]
    ]

    rows = sheet.iter_rows(
        values_only=True
    )

    try:

        headers = next(rows)

    except StopIteration:

        return []

    headers = [
        str(x).strip()
        if x is not None
        else ""
        for x in headers
    ]

    result = []

    for row in rows:

        item = {}

        for index, header in enumerate(
            headers
        ):

            if not header:
                continue

            value = (
                row[index]
                if index < len(row)
                else ""
            )

            item[header] = (
                ""
                if value is None
                else str(value).strip()
            )

        if any(
            str(v).strip()
            for v in item.values()
        ):

            result.append(item)

    workbook.close()

    return result


# ============================================================
# ЧТЕНИЕ CSV
# ============================================================

def read_csv_file(file):

    file.seek(0)

    raw = file.read()

    try:

        text = raw.decode(
            "utf-8-sig"
        )

    except UnicodeDecodeError:

        text = raw.decode(
            "cp1251",
            errors="replace"
        )

    sample = text[:5000]

    try:

        dialect = csv.Sniffer().sniff(
            sample,
            delimiters=";,|\t"
        )

        delimiter = dialect.delimiter

    except Exception:

        delimiter = ";"

    reader = csv.DictReader(
        text.splitlines(),
        delimiter=delimiter
    )

    result = []

    for row in reader:

        item = {}

        for key, value in row.items():

            if key is None:
                continue

            item[
                str(key).strip()
            ] = (
                ""
                if value is None
                else str(value).strip()
            )

        if any(
            str(v).strip()
            for v in item.values()
        ):

            result.append(item)

    return result


# ============================================================
# ЗАГРУЗКА ТАБЛИЦЫ
# ============================================================

def load_table(file):

    name = file.name.lower()

    if name.endswith(".xlsx"):

        return read_xlsx(file)

    if name.endswith(".csv"):

        return read_csv_file(file)

    raise Exception(
        "Поддерживаются XLSX и CSV."
    )


# ============================================================
# НОРМАЛИЗАЦИЯ НАЗВАНИЯ КОЛОНКИ
# ============================================================

def normalize_column_name(
    name
):

    if name is None:
        return ""

    value = str(
        name
    ).strip().lower()

    value = (
        value
        .replace("ё", "е")
        .replace("\xa0", " ")
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value


def find_column(
    columns,
    variants
):

    normalized = {}

    for column in columns:

        normalized[
            normalize_column_name(
                column
            )
        ] = column

    # Точное совпадение
    for variant in variants:

        key = normalize_column_name(
            variant
        )

        if key in normalized:

            return normalized[key]

    # Частичное
    for column in columns:

        c = normalize_column_name(
            column
        )

        for variant in variants:

            v = normalize_column_name(
                variant
            )

            if v in c:

                return column

    return None


# ============================================================
# ИНДЕКС API
# ============================================================

def build_api_index(rows):

    if not rows:

        raise Exception(
            "Выгрузка API пустая."
        )

    columns = list(
        rows[0].keys()
    )

    order_column = find_column(
        columns,
        [
            "Номер отправления",
            "posting number",
            "posting_number",
            "Номер отправления Ozon",
            "Отправление"
        ]
    )

    article_column = find_column(
        columns,
        [
            "Артикул продавца",
            "Артикул",
            "offer_id",
            "Offer ID"
        ]
    )

    qty_column = find_column(
        columns,
        [
            "Количество",
            "Кол-во",
            "Кол во",
            "Qty",
            "quantity"
        ]
    )

    sku_column = find_column(
        columns,
        [
            "SKU Ozon",
            "SKU",
            "product_id"
        ]
    )

    if not order_column:

        raise Exception(
            "Не найдена колонка "
            "'Номер отправления'."
        )

    if not article_column:

        raise Exception(
            "Не найдена колонка "
            "'Артикул продавца'."
        )

    if not qty_column:

        raise Exception(
            "Не найдена колонка "
            "'Количество'."
        )

    index = {}

    for row in rows:

        order = normalize_order(
            row.get(
                order_column,
                ""
            )
        )

        if not order:
            continue

        item = {

            "order": order,

            "article": str(
                row.get(
                    article_column,
                    ""
                )
            ).strip(),

            "qty": str(
                row.get(
                    qty_column,
                    ""
                )
            ).strip(),

            "sku": str(
                row.get(
                    sku_column,
                    ""
                )
            ).strip()
        }

        index[
            order
        ] = item

        numeric = numeric_order_key(
            order
        )

        if numeric:

            index[
                "__NUM__" + numeric
            ] = item

    return index


def find_api_item(
    order,
    api_index
):

    normalized = normalize_order(
        order
    )

    if normalized in api_index:

        return api_index[
            normalized
        ]

    numeric = numeric_order_key(
        normalized
    )

    if numeric:

        return api_index.get(
            "__NUM__" + numeric
        )

    return None


# ============================================================
# СОЗДАНИЕ ИНФОРМАЦИОННОЙ СТРАНИЦЫ
# ============================================================

def create_info_page(
    width,
    height,
    item
):

    packet = BytesIO()

    c = canvas.Canvas(
        packet,
        pagesize=(
            width,
            height
        )
    )

    x = 10

    # ========================================================
    # ЗАКАЗ
    # ========================================================

    c.setFont(
        font_name,
        9
    )

    c.drawString(
        x,
        height - 17,
        "Заказ: " +
        item["order"]
    )

    c.line(
        x,
        height - 20,
        width - x,
        height - 20
    )

    # ========================================================
    # АРТИКУЛ
    # ========================================================

    article = item[
        "article"
    ]

    c.setFont(
        font_name,
        13
    )

    if len(article) > 28:

        article = (
            article[:25]
            + "..."
        )

    c.drawString(
        x,
        height - 37,
        "Арт: " +
        article
    )

    # ========================================================
    # SKU
    # ========================================================

    sku = item.get(
        "sku",
        ""
    )

    if sku:

        c.setFont(
            font_name,
            7
        )

        c.drawString(
            x,
            height - 49,
            "SKU: " +
            sku
        )

    # ========================================================
    # КОЛИЧЕСТВО
    # ========================================================

    c.setFont(
        font_name,
        24
    )

    c.drawString(
        x,
        15,
        "КОЛ-ВО: " +
        item["qty"]
    )

    c.save()

    packet.seek(0)

    return PdfReader(
        packet
    ).pages[0]


# ============================================================
# ПОИСК НЕРАСПОЗНАННЫХ СТРАНИЦ
# ============================================================

def is_unresolved_page(
    text
):

    if not text:

        return False

    normalized = (
        text
        .replace(
            "\xa0",
            " "
        )
        .lower()
    )

    return (
        "не распознан"
        in normalized
    )


# ============================================================
# ОСНОВНОЙ ИНТЕРФЕЙС
# ============================================================

st.divider()

st.subheader(
    "Загрузите три файла"
)

col1, col2, col3 = st.columns(3)

with col1:

    stage1_file = st.file_uploader(
        "1️⃣ PDF после Этапа 1",
        type=["pdf"]
    )

with col2:

    picklist_file = st.file_uploader(
        "2️⃣ PDF листа подбора",
        type=["pdf"]
    )

with col3:

    api_file = st.file_uploader(
        "3️⃣ XLSX / CSV Ozon API",
        type=[
            "xlsx",
            "csv"
        ]
    )


# ============================================================
# ЗАПУСК
# ============================================================

if (
    stage1_file
    and picklist_file
    and api_file
):

    if st.button(
        "🔎 НАЙТИ НЕРАСПОЗНАННЫЕ ЭТИКЕТКИ",
        type="primary",
        use_container_width=True
    ):

        try:

            # =================================================
            # API
            # =================================================

            with st.spinner(
                "Читаю выгрузку Ozon API..."
            ):

                api_rows = load_table(
                    api_file
                )

                api_index = build_api_index(
                    api_rows
                )

            st.success(
                f"API: найдено "
                f"{len(api_rows)} строк"
            )

            # =================================================
            # ЛИСТ ПОДБОРА
            # =================================================

            with st.spinner(
                "Анализирую PDF листа подбора..."
            ):

                mappings = (
                    find_picklist_mapping(
                        picklist_file
                    )
                )

            st.info(
                f"🔎 Найдено связок "
                f"«отправление → этикетка»: "
                f"{len(mappings)}"
            )

            # =================================================
            # ТАБЛИЦА СВЯЗОК
            # =================================================

            result_rows = []

            for mapping in mappings:

                item = find_api_item(
                    mapping["order"],
                    api_index
                )

                result_rows.append(
                    {
                        "Страница листа":
                            mapping["page"],

                        "№ этикетки":
                            mapping["label_key"],

                        "Номер отправления":
                            mapping["order"],

                        "Артикул":
                            item["article"]
                            if item
                            else "НЕ НАЙДЕН",

                        "SKU":
                            item["sku"]
                            if item
                            else "",

                        "Количество":
                            item["qty"]
                            if item
                            else "НЕ НАЙДЕНО",

                        "Метод":
                            mapping["method"]
                    }
                )

            # =================================================
            # ПРЕДПРОСМОТР
            # =================================================

            st.subheader(
                "🔎 Найденные соответствия"
            )

            if result_rows:

                st.dataframe(
                    result_rows,
                    use_container_width=True
                )

            else:

                st.error(
                    """
                    Не найдено ни одной связки.

                    Проверьте PDF листа подбора.
                    """
                )

            # =================================================
            # СЛОВАРЬ:
            #
            # LABEL KEY → API ITEM
            # =================================================

            label_to_item = {}

            for mapping in mappings:

                item = find_api_item(
                    mapping["order"],
                    api_index
                )

                if not item:
                    continue

                label_key = (
                    mapping["label_key"]
                )

                label_to_item[
                    label_key
                ] = item

            # =================================================
            # ЧИТАЕМ PDF ЭТАПА 1
            # =================================================

            stage1_file.seek(0)

            reader = PdfReader(
                stage1_file
            )

            writer = PdfWriter()

            total_pages = len(
                reader.pages
            )

            unresolved_total = 0
            fixed_total = 0
            still_unresolved = 0

            progress = st.progress(
                0
            )

            status = st.empty()

            # =================================================
            # ОБРАБОТКА PDF ЭТАПА 1
            # =================================================

            for i, page in enumerate(
                reader.pages
            ):

                status.text(
                    f"Обработка страницы "
                    f"{i + 1} / "
                    f"{total_pages}"
                )

                try:

                    text = (
                        page.extract_text()
                        or ""
                    )

                except Exception:

                    text = ""

                # =================================================
                # ОРИГИНАЛЬНАЯ ЭТИКЕТКА
                # =================================================

                writer.add_page(
                    page
                )

                # =================================================
                # НЕРАСПОЗНАННАЯ ИНФОРМАЦИОННАЯ СТРАНИЦА
                # =================================================

                if is_unresolved_page(
                    text
                ):

                    unresolved_total += 1

                    # ---------------------------------------------
                    # На этой странице самого номера 2549 обычно
                    # уже нет, потому что это техническая страница
                    # первого этапа.
                    #
                    # Поэтому определяем номер этикетки
                    # по ПРЕДЫДУЩЕЙ странице.
                    # ---------------------------------------------

                    label_key = ""

                    if i > 0:

                        previous_page = (
                            reader.pages[i - 1]
                        )

                        try:

                            previous_text = (
                                previous_page.extract_text()
                                or ""
                            )

                        except Exception:

                            previous_text = ""

                        found_keys = (
                            find_label_keys(
                                previous_text
                            )
                        )

                        if found_keys:

                            label_key = (
                                found_keys[0]
                            )

                    # ---------------------------------------------
                    # Если на предыдущей странице номер не найден,
                    # пробуем определить его из текущего PDF
                    # другим способом.
                    # ---------------------------------------------

                    item = None

                    if label_key:

                        item = (
                            label_to_item.get(
                                label_key
                            )
                        )

                    # =================================================
                    # НАШЛИ
                    # =================================================

                    if item:

                        fixed_total += 1

                        new_info_page = (
                            create_info_page(
                                width=float(
                                    page.mediabox.width
                                ),
                                height=float(
                                    page.mediabox.height
                                ),
                                item=item
                            )
                        )

                        writer.add_page(
                            new_info_page
                        )

                    # =================================================
                    # НЕ НАШЛИ
                    # =================================================

                    else:

                        still_unresolved += 1

                        # Оставляем оригинальную
                        # страницу НЕ РАСПОЗНАН

                        writer.add_page(
                            page
                        )

                else:

                    # Обычная информационная страница
                    writer.add_page(
                        page
                    )

                progress.progress(
                    (i + 1) /
                    total_pages
                )

            status.text(
                "✅ Этап 2 завершён"
            )

            # ====================================================
            # СТАТИСТИКА
            # ====================================================

            st.divider()

            c1, c2, c3 = st.columns(3)

            c1.metric(
                "НЕ РАСПОЗНАНО на Этапе 1",
                unresolved_total
            )

            c2.metric(
                "Исправлено",
                fixed_total
            )

            c3.metric(
                "Осталось",
                still_unresolved
            )

            # ====================================================
            # РЕЗУЛЬТАТ
            # ====================================================

            if fixed_total:

                st.success(
                    f"✅ Исправлено "
                    f"{fixed_total} "
                    f"этикеток."
                )

            if still_unresolved:

                st.warning(
                    f"⚠️ Осталось "
                    f"{still_unresolved} "
                    "неопознанных этикеток."
                )

            # ====================================================
            # СОХРАНЯЕМ
            # ====================================================

            output = BytesIO()

            writer.write(
                output
            )

            output.seek(0)

            st.download_button(
                "📥 Скачать готовый PDF",
                output,
                "Ozon_Final_Labels.pdf",
                "application/pdf",
                type="primary",
                use_container_width=True
            )

            # ====================================================
            # ОТЛАДКА
            # ====================================================

            with st.expander(
                "🔧 Отладка — найденные номера этикеток"
            ):

                if mappings:

                    for mapping in mappings:

                        item = find_api_item(
                            mapping["order"],
                            api_index
                        )

                        st.write(
                            {
                                "Страница":
                                    mapping["page"],

                                "Этикетка":
                                    mapping["label_key"],

                                "Отправление":
                                    mapping["order"],

                                "Артикул":
                                    item["article"]
                                    if item
                                    else "—",

                                "Количество":
                                    item["qty"]
                                    if item
                                    else "—",

                                "Метод":
                                    mapping["method"]
                            }
                        )

                else:

                    st.write(
                        "Связки не найдены."
                    )

        except Exception as e:

            st.error(
                "❌ Ошибка:\n\n"
                + str(e)
            )

            with st.expander(
                "Техническая информация"
            ):

                st.exception(e)


# ============================================================
# ИНСТРУКЦИЯ
# ============================================================

if not (
    stage1_file
    and picklist_file
    and api_file
):

    st.info(
        """
        ### Как работать

        **1.** Сначала запустите `app.py`.

        **2.** Получите:

        `Ozon_Etap_1.pdf`

        **3.** Найдите исходный PDF:

        `Лист подбора`

        **4.** Возьмите тот же XLSX/CSV,
        который использовался на первом этапе.

        **5.** Загрузите сюда все три файла.

        **6.** Нажмите:

        **«НАЙТИ НЕРАСПОЗНАННЫЕ ЭТИКЕТКИ»**

        Пример:

        `II5010320 2549`

        будет сопоставляться с листом подбора,
        где рядом находится:

        `87180955-0554-25`

        После этого номер отправления ищется
        в выгрузке API и подтягиваются:

        `Артикул`
        
        `SKU`

        `Количество`
        """
    )
