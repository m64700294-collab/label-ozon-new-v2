import io
import re
import os
import urllib.request
from collections import defaultdict

import streamlit as st
import pdfplumber

from pypdf import PdfReader, PdfWriter

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# НАСТРОЙКИ
# ============================================================

st.set_page_config(
    page_title="Ozon — Этикетки + Лист подбора",
    page_icon="🖨️",
    layout="wide"
)

FONT_PATH = "Roboto_Full_Final.ttf"
FONT_URL = "https://cdnjs.cloudflare.com/ajax/libs/roboto/2.138/fonts/ttf/Roboto-Regular.ttf"


# ============================================================
# ШРИФТ
# ============================================================

def ensure_font():
    if os.path.exists(FONT_PATH):
        return FONT_PATH

    try:
        urllib.request.urlretrieve(FONT_URL, FONT_PATH)
        return FONT_PATH
    except Exception:
        return None


FONT_FILE = ensure_font()

if FONT_FILE:
    try:
        pdfmetrics.registerFont(
            TTFont("Roboto", FONT_FILE)
        )
        PDF_FONT = "Roboto"
    except Exception:
        PDF_FONT = "Helvetica"
else:
    PDF_FONT = "Helvetica"


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================

def normalize_spaces(value):
    if value is None:
        return ""

    value = str(value)

    value = value.replace("\xa0", " ")
    value = value.replace("\u200b", "")
    value = value.replace("\ufeff", "")

    value = re.sub(r"[ \t]+", " ", value)

    return value.strip()


def normalize_text(value):
    value = normalize_spaces(value)

    value = value.lower()

    # Кириллица
    value = value.replace("і", "и")
    value = value.replace("ё", "е")

    return value


def clean_identifier(value):
    """
    Нормализация идентификатора.

    Пример:

    ii5010320 2537
    ->
    ii50103202537

    150103202537
    ->
    150103202537
    """

    if value is None:
        return ""

    value = str(value).strip()

    value = value.replace("\xa0", "")
    value = value.replace(" ", "")
    value = value.replace("\n", "")
    value = value.replace("\r", "")
    value = value.replace("\t", "")

    return value.lower()


def digits_only(value):
    if not value:
        return ""

    return re.sub(r"\D", "", str(value))


# ============================================================
# НОМЕРА ОТПРАВЛЕНИЙ
# ============================================================

ORDER_PATTERN = re.compile(
    r"\d{8,15}-\d{4}-\d+",
    re.IGNORECASE
)


def extract_standard_orders(text):
    """
    Ищет стандартные номера Ozon:

    78277691-0407-1

    В PDF номер может быть разбит:

    78277691
    -0407-1

    Поэтому сначала склеиваем строки.
    """

    if not text:
        return []

    text = str(text)

    # Убираем мусорные пробелы
    text = text.replace("\xa0", " ")

    # Сначала нормальный поиск
    found = ORDER_PATTERN.findall(text)

    if found:
        return list(dict.fromkeys(found))

    # Попытка восстановить разбитый номер
    lines = [
        normalize_spaces(x)
        for x in text.splitlines()
        if normalize_spaces(x)
    ]

    result = []

    for i in range(len(lines) - 1):

        a = re.sub(r"\s+", "", lines[i])
        b = re.sub(r"\s+", "", lines[i + 1])

        if re.fullmatch(r"\d{8,15}", a) and re.fullmatch(
            r"-\d{4}-\d+", b
        ):
            order = a + b

            if order not in result:
                result.append(order)

    # Дополнительный вариант:
    # номер может оказаться внутри одной строки
    flat = re.sub(r"\s+", "", text)

    m = re.findall(
        r"(\d{8,15})(-\d{4}-\d+)",
        flat
    )

    for a, b in m:
        order = a + b

        if order not in result:
            result.append(order)

    return result


def get_short_code(order):
    """
    Для:

    78277691-0407-1

    получаем:

    7691

    Это последние 4 цифры первой части.
    """

    if not order:
        return ""

    order = clean_identifier(order)

    if "-" in order:
        first = order.split("-")[0]
        digits = digits_only(first)

        if len(digits) >= 4:
            return digits[-4:]

    digits = digits_only(order)

    if len(digits) >= 4:
        return digits[-4:]

    return ""


def get_last4(value):
    digits = digits_only(value)

    if len(digits) >= 4:
        return digits[-4:]

    return ""


# ============================================================
# ШТРИХКОДЫ С ЭТИКЕТКИ
# ============================================================

def extract_barcode_identifiers(text):
    """
    Ищем номера вида:

    ii5010320 2537

    После очистки:

    ii50103202537

    """

    if not text:
        return []

    text = str(text)

    result = []

    # Варианты ii + цифры + пробелы
    matches = re.findall(
        r"\bii[\s\d]{8,30}",
        text,
        flags=re.IGNORECASE
    )

    for item in matches:

        cleaned = clean_identifier(item)

        # Оставляем только ii + цифры
        m = re.match(
            r"(ii\d+)",
            cleaned,
            flags=re.IGNORECASE
        )

        if m:
            value = m.group(1)

            if value not in result:
                result.append(value)

    return result


# ============================================================
# ОБЩИЕ ИДЕНТИФИКАТОРЫ ЗАПИСИ
# ============================================================

def build_record_identifiers(orders, raw_number_cell, label_code):
    """
    Собирает ВСЕ возможные ключи записи.

    Например:

    78277691-0407-1
    7691

    или:

    150103202537
    ii50103202537
    2537
    """

    identifiers = set()

    # Стандартные номера
    for order in orders:
        cleaned = clean_identifier(order)

        if cleaned:
            identifiers.add(cleaned)

        # numeric key
        digits = digits_only(cleaned)

        if digits:
            identifiers.add(digits)

    # Всё из ячейки номера
    if raw_number_cell:
        raw = str(raw_number_cell)

        for line in raw.splitlines():

            line = normalize_spaces(line)

            if not line:
                continue

            cleaned = clean_identifier(line)

            if cleaned:
                identifiers.add(cleaned)

            digits = digits_only(cleaned)

            if digits:
                identifiers.add(digits)

            # barcode ii...
            if cleaned.startswith("ii"):
                identifiers.add(cleaned)

    # Этикетка
    if label_code:
        label_code = clean_identifier(label_code)

        if label_code:
            identifiers.add(label_code)

    return identifiers


# ============================================================
# РАЗБОР СТРОКИ ТАБЛИЦЫ
# ============================================================

def normalize_header(value):
    if value is None:
        return ""

    value = normalize_text(value)

    value = value.replace("№", "")
    value = value.replace(".", "")

    return value.strip()


def find_column_indexes(header):
    """
    Находим реальные колонки по заголовкам.

    Нужны:

    Номер отправления
    Товар
    Артикул
    Кол-во
    Этикетка
    """

    indexes = {}

    for i, value in enumerate(header):

        h = normalize_header(value)

        if not h:
            continue

        # Номер отправления
        if (
            "номер отправления" in h
            or "номер с этикетки" in h
            or h == "номер"
        ):
            indexes.setdefault("number", i)

        # Товар
        elif h == "товар" or "товар" in h:
            indexes.setdefault("product", i)

        # Артикул
        elif "артикул" in h:
            indexes.setdefault("article", i)

        # Количество
        elif (
            "кол-во" in h
            or "кол во" in h
            or "количество" in h
        ):
            indexes.setdefault("qty", i)

        # Этикетка
        elif "этикетка" in h:
            indexes.setdefault("label", i)

    return indexes


# ============================================================
# ПОИСК ТАБЛИЦ
# ============================================================

def extract_tables_from_page(page):
    """
    Несколько стратегий pdfplumber.

    Сначала пробуем полноценную табличную структуру.
    """

    strategies = [

        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 8,
            "snap_tolerance": 5,
            "join_tolerance": 5,
            "edge_min_length": 20,
        },

        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "text",
            "intersection_tolerance": 8,
            "snap_tolerance": 5,
            "join_tolerance": 5,
        },

        {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "intersection_tolerance": 8,
            "snap_tolerance": 5,
            "join_tolerance": 5,
            "min_words_vertical": 2,
            "min_words_horizontal": 1,
        }
    ]

    for settings in strategies:

        try:
            tables = page.extract_tables(
                table_settings=settings
            )

            if tables:
                useful = [
                    t for t in tables
                    if t and len(t) >= 2
                ]

                if useful:
                    return useful

        except Exception:
            pass

    return []


# ============================================================
# ПАРСИНГ ТАБЛИЦЫ
# ============================================================

def parse_table_rows(table):
    """
    Превращает таблицу в записи.

    Ключевой момент:
    НЕ пытаемся угадать Артикул или Кол-во
    из текста товара.

    Они берутся только из соответствующих колонок.
    """

    if not table:
        return []

    # Ищем строку заголовка
    header_index = None
    column_indexes = None

    for row_index, row in enumerate(table):

        if not row:
            continue

        indexes = find_column_indexes(row)

        # Достаточно хотя бы номера + артикула
        if (
            "number" in indexes
            and "article" in indexes
        ):
            header_index = row_index
            column_indexes = indexes
            break

    if header_index is None:
        return []

    records = []

    for row in table[header_index + 1:]:

        if not row:
            continue

        # Расширяем строку
        row = list(row)

        while len(row) < max(column_indexes.values()) + 1:
            row.append("")

        number_cell = row[column_indexes["number"]]

        product = ""

        if "product" in column_indexes:
            product = normalize_spaces(
                row[column_indexes["product"]]
            )

        article = normalize_spaces(
            row[column_indexes["article"]]
        )

        qty = ""

        if "qty" in column_indexes:
            qty = normalize_spaces(
                row[column_indexes["qty"]]
            )

        label = ""

        if "label" in column_indexes:
            label = normalize_spaces(
                row[column_indexes["label"]]
            )

        # Пропускаем пустые строки
        if not (
            normalize_spaces(number_cell)
            or article
            or product
        ):
            continue

        # Нормализуем количество.
        # Берем число ТОЛЬКО из колонки Кол-во.
        qty_match = re.search(
            r"\b\d{1,4}\b",
            qty
        )

        if qty_match:
            qty = qty_match.group(0)
        else:
            qty = "1"

        # Артикул:
        # только содержимое колонки Артикул
        article = normalize_spaces(article)

        # Этикетка:
        # только содержимое колонки Этикетка
        label_match = re.search(
            r"\d{4}",
            label
        )

        if label_match:
            label = label_match.group(0)
        else:
            label = ""

        # Заказы
        orders = extract_standard_orders(
            str(number_cell)
        )

        # Дополнительные номера из ячейки
        raw_numbers = []

        for line in str(number_cell).splitlines():

            line = normalize_spaces(line)

            if line:
                raw_numbers.append(line)

        # Если номер стандартный не нашли,
        # всё равно оставляем содержимое ячейки.
        if not orders:
            for line in raw_numbers:

                cleaned = clean_identifier(line)

                if cleaned.startswith("ii"):
                    continue

                if (
                    len(digits_only(cleaned)) >= 8
                ):
                    raw_numbers.append(cleaned)

        identifiers = build_record_identifiers(
            orders,
            number_cell,
            label
        )

        # Добавляем короткий код
        for order in orders:

            short_code = get_short_code(order)

            if short_code:
                identifiers.add(short_code)

        # Добавляем последние 4 цифры всех номеров
        for value in raw_numbers:

            last4 = get_last4(value)

            if last4:
                identifiers.add(last4)

        record = {
            "orders": orders,
            "raw_number": str(number_cell),
            "product": product,
            "article": article,
            "qty": qty,
            "label": label,
            "identifiers": identifiers,
        }

        records.append(record)

    return records


# ============================================================
# РЕЗЕРВНЫЙ ПАРСЕР БЛОКОВ
# ============================================================

def parse_blocks_fallback(page):
    """
    Запасной вариант, если pdfplumber не смог
    распознать таблицу.

    ВАЖНО:

    Здесь мы также НЕ пытаемся брать артикул
    из названия товара.

    Сначала ищем явные заголовки и табличные строки.
    """

    text = page.extract_text(
        x_tolerance=2,
        y_tolerance=3,
        layout=True
    )

    if not text:
        return []

    lines = [
        normalize_spaces(line)
        for line in text.splitlines()
    ]

    lines = [
        line for line in lines
        if line
    ]

    records = []

    # Ищем стандартные номера
    orders = extract_standard_orders(text)

    # В fallback используем только если
    # в тексте явно присутствуют соответствующие
    # заголовки.
    #
    # Этот режим специально консервативный.

    if not orders:
        return []

    # Пытаемся найти четырехзначные этикетки
    labels = []

    for line in lines:

        m = re.fullmatch(
            r"\d{4}",
            line
        )

        if m:
            labels.append(m.group(0))

    # Если есть столько же этикеток, сколько заказов,
    # можем создать минимальные записи.
    if len(labels) == len(orders):

        for order, label in zip(orders, labels):

            records.append({
                "orders": [order],
                "raw_number": order,
                "product": "",
                "article": "",
                "qty": "1",
                "label": label,
                "identifiers": {
                    clean_identifier(order),
                    digits_only(order),
                    get_short_code(order),
                    label,
                },
            })

    return records


# ============================================================
# ПАРСИНГ ВСЕГО ЛИСТА ПОДБОРА
# ============================================================

def parse_assembly_pdf(pdf_bytes):
    """
    Возвращает:

    records
    label_map
    identifier_map
    """

    records = []

    with pdfplumber.open(
        io.BytesIO(pdf_bytes)
    ) as pdf:

        for page_number, page in enumerate(pdf.pages, start=1):

            tables = extract_tables_from_page(page)

            page_records = []

            for table in tables:

                parsed = parse_table_rows(table)

                if parsed:
                    page_records.extend(parsed)

            # Если таблица не распозналась
            if not page_records:

                page_records = parse_blocks_fallback(
                    page
                )

            records.extend(page_records)

    # --------------------------------------------------------
    # Удаляем дубли
    # --------------------------------------------------------

    unique = []

    seen = set()

    for record in records:

        key = (
            tuple(sorted(record["orders"])),
            record["article"],
            record["label"],
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(record)

    records = unique

    # --------------------------------------------------------
    # Карта по идентификаторам
    # --------------------------------------------------------

    identifier_map = defaultdict(list)
    label_map = defaultdict(list)

    for record in records:

        for identifier in record["identifiers"]:

            identifier = clean_identifier(identifier)

            if identifier:
                identifier_map[identifier].append(
                    record
                )

        if record["label"]:

            label_map[
                clean_identifier(record["label"])
            ].append(record)

    return records, identifier_map, label_map


# ============================================================
# ПОИСК ЗАПИСИ
# ============================================================

def find_unique_record(
    candidates
):
    """
    Возвращает запись только если она однозначна.
    """

    if not candidates:
        return None

    unique = []

    seen = set()

    for record in candidates:

        key = (
            record.get("article", ""),
            record.get("label", ""),
            tuple(record.get("orders", []))
        )

        if key not in seen:
            seen.add(key)
            unique.append(record)

    if len(unique) == 1:
        return unique[0]

    return None


def find_record_for_label(
    label_text,
    identifier_map,
    label_map
):
    """
    Основная логика сопоставления.

    ПРИОРИТЕТ:

    1. Полный номер отправления
    2. ii-штрихкод
    3. числовой идентификатор
    4. 4 цифры Этикетка

    Никакого сопоставления по названию товара.
    Никакого угадывания артикула.
    """

    if not label_text:
        return None, "нет текста"

    # --------------------------------------------------------
    # 1. Стандартный номер Ozon
    # --------------------------------------------------------

    orders = extract_standard_orders(
        label_text
    )

    for order in orders:

        key = clean_identifier(order)

        candidates = identifier_map.get(
            key,
            []
        )

        record = find_unique_record(
            candidates
        )

        if record:
            return record, "номер отправления"

        # digits only
        numeric = digits_only(order)

        candidates = identifier_map.get(
            numeric,
            []
        )

        record = find_unique_record(
            candidates
        )

        if record:
            return record, "numeric"

        # короткий код
        short = get_short_code(order)

        if short:

            candidates = label_map.get(
                short,
                []
            )

            record = find_unique_record(
                candidates
            )

            if record:
                return record, "этикетка"

    # --------------------------------------------------------
    # 2. ii barcode
    # --------------------------------------------------------

    barcodes = extract_barcode_identifiers(
        label_text
    )

    for barcode in barcodes:

        key = clean_identifier(barcode)

        candidates = identifier_map.get(
            key,
            []
        )

        record = find_unique_record(
            candidates
        )

        if record:
            return record, "штрихкод"

        # numeric barcode
        numeric = digits_only(barcode)

        candidates = identifier_map.get(
            numeric,
            []
        )

        record = find_unique_record(
            candidates
        )

        if record:
            return record, "штрихкод numeric"

        # Последние 4 цифры
        last4 = get_last4(barcode)

        if last4:

            candidates = label_map.get(
                last4,
                []
            )

            record = find_unique_record(
                candidates
            )

            if record:
                return record, "этикетка 4 цифры"

    # --------------------------------------------------------
    # 3. Любые четырехзначные коды из текста
    # --------------------------------------------------------

    four_digit_codes = re.findall(
        r"(?<!\d)(\d{4})(?!\d)",
        label_text
    )

    # Убираем дубли
    four_digit_codes = list(
        dict.fromkeys(four_digit_codes)
    )

    for code in four_digit_codes:

        candidates = label_map.get(
            code,
            []
        )

        record = find_unique_record(
            candidates
        )

        if record:
            return record, "этикетка 4 цифры"

    return None, "не найдено"


# ============================================================
# ИНФОРМАЦИОННАЯ СТРАНИЦА
# ============================================================

def create_info_page(
    width,
    height,
    order,
    article,
    product,
    qty
):
    """
    Создает страницу того же размера,
    что оригинальная этикетка.
    """

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=(width, height)
    )

    # --------------------------------------------------------
    # Размеры
    # --------------------------------------------------------

    margin = 20

    y = height - margin

    # --------------------------------------------------------
    # Заказ
    # --------------------------------------------------------

    c.setFont(
        PDF_FONT,
        12
    )

    c.drawString(
        margin,
        y,
        f"Заказ: {order}"
    )

    y -= 25

    # --------------------------------------------------------
    # Артикул
    # --------------------------------------------------------

    c.drawString(
        margin,
        y,
        f"Арт: {article or '-'}"
    )

    y -= 25

    # --------------------------------------------------------
    # Товар
    # --------------------------------------------------------

    # Только первые 20 символов
    product_short = normalize_spaces(
        product
    )[:20]

    c.drawString(
        margin,
        y,
        f"Товар: {product_short}"
    )

    y -= 25

    # --------------------------------------------------------
    # Количество
    # --------------------------------------------------------

    c.drawString(
        margin,
        y,
        f"КОЛ-ВО: {qty or '1'}"
    )

    c.showPage()
    c.save()

    buffer.seek(0)

    return buffer.getvalue()


# ============================================================
# СКЛЕЙКА
# ============================================================

def process_files(
    labels_bytes,
    assembly_bytes
):

    # --------------------------------------------------------
    # Парсим лист подбора
    # --------------------------------------------------------

    records, identifier_map, label_map = (
        parse_assembly_pdf(
            assembly_bytes
        )
    )

    # --------------------------------------------------------
    # Статистика
    # --------------------------------------------------------

    result = io.BytesIO()

    writer = PdfWriter()

    label_reader = PdfReader(
        io.BytesIO(labels_bytes)
    )

    matched = 0
    not_found = 0

    matches_by_method = defaultdict(int)

    diagnostics = []

    # --------------------------------------------------------
    # Каждая страница этикетки
    # --------------------------------------------------------

    with pdfplumber.open(
        io.BytesIO(labels_bytes)
    ) as labels_pdf:

        for page_index, pdf_page in enumerate(
            labels_pdf.pages
        ):

            text = pdf_page.extract_text(
                x_tolerance=2,
                y_tolerance=3,
                layout=True
            ) or ""

            # ------------------------------------------------
            # Ищем соответствующую запись
            # ------------------------------------------------

            record, method = find_record_for_label(
                text,
                identifier_map,
                label_map
            )

            # ------------------------------------------------
            # Оригинальная страница
            # ------------------------------------------------

            original_page = label_reader.pages[
                page_index
            ]

            writer.add_page(
                original_page
            )

            # ------------------------------------------------
            # Размер страницы
            # ------------------------------------------------

            width = float(
                original_page.mediabox.width
            )

            height = float(
                original_page.mediabox.height
            )

            # ------------------------------------------------
            # Если нашли
            # ------------------------------------------------

            if record:

                matched += 1

                matches_by_method[
                    method
                ] += 1

                orders = record.get(
                    "orders",
                    []
                )

                order = (
                    orders[0]
                    if orders
                    else ""
                )

                info_pdf = create_info_page(
                    width=width,
                    height=height,
                    order=order,
                    article=record.get(
                        "article",
                        ""
                    ),
                    product=record.get(
                        "product",
                        ""
                    ),
                    qty=record.get(
                        "qty",
                        "1"
                    )
                )

                info_reader = PdfReader(
                    io.BytesIO(info_pdf)
                )

                writer.add_page(
                    info_reader.pages[0]
                )

                diagnostics.append({
                    "page": page_index + 1,
                    "status": "OK",
                    "method": method,
                    "order": order,
                    "article": record.get(
                        "article",
                        ""
                    ),
                    "product": record.get(
                        "product",
                        ""
                    )[:20],
                    "qty": record.get(
                        "qty",
                        "1"
                    ),
                    "label": record.get(
                        "label",
                        ""
                    )
                })

            # ------------------------------------------------
            # Не нашли
            # ------------------------------------------------

            else:

                not_found += 1

                # Создаем страницу-заглушку
                info_pdf = create_info_page(
                    width=width,
                    height=height,
                    order="НЕ НАЙДЕН",
                    article="",
                    product="",
                    qty="1"
                )

                info_reader = PdfReader(
                    io.BytesIO(info_pdf)
                )

                writer.add_page(
                    info_reader.pages[0]
                )

                diagnostics.append({
                    "page": page_index + 1,
                    "status": "NOT FOUND",
                    "method": method,
                    "order": "",
                    "article": "",
                    "product": "",
                    "qty": "",
                    "label": ""
                })

    # --------------------------------------------------------
    # Сохраняем
    # --------------------------------------------------------

    writer.write(result)

    result.seek(0)

    return (
        result.getvalue(),
        records,
        matched,
        not_found,
        matches_by_method,
        diagnostics
    )


# ============================================================
# UI
# ============================================================

st.title(
    "🖨️ Склейка: Этикетки + Лист подбора"
)

st.markdown(
    """
Загрузите два PDF:

1. **Этикетки Ozon**
2. **Лист подбора Ozon**

Для каждой этикетки будет добавлена информационная страница.
"""
)

col1, col2 = st.columns(2)

with col1:

    labels_file = st.file_uploader(
        "📦 Этикетки",
        type=["pdf"],
        key="labels"
    )

with col2:

    assembly_file = st.file_uploader(
        "📋 Лист подбора",
        type=["pdf"],
        key="assembly"
    )


# ============================================================
# ОБРАБОТКА
# ============================================================

if st.button(
    "🚀 Сформировать PDF",
    type="primary",
    use_container_width=True
):

    if not labels_file:
        st.error(
            "Загрузите PDF с этикетками."
        )
        st.stop()

    if not assembly_file:
        st.error(
            "Загрузите PDF с листом подбора."
        )
        st.stop()

    with st.spinner(
        "Читаю лист подбора и сопоставляю этикетки..."
    ):

        try:

            output, records, matched, not_found, methods, diagnostics = (
                process_files(
                    labels_file.getvalue(),
                    assembly_file.getvalue()
                )
            )

        except Exception as e:

            st.exception(e)
            st.stop()

    # ========================================================
    # СТАТИСТИКА
    # ========================================================

    st.success(
        f"Готово. Найдено: {matched}. "
        f"Не найдено: {not_found}."
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Записей в листе подбора",
            len(records)
        )

    with col2:
        st.metric(
            "Этикеток сопоставлено",
            matched
        )

    with col3:
        st.metric(
            "Не сопоставлено",
            not_found
        )

    # ========================================================
    # МЕТОДЫ МАППИНГА
    # ========================================================

    if methods:

        st.subheader(
            "🔗 Способы сопоставления"
        )

        for method, count in methods.items():

            st.write(
                f"**{method}:** {count}"
            )

    # ========================================================
    # ДИАГНОСТИКА
    # ========================================================

    st.subheader(
        "🔎 Первые результаты маппинга"
    )

    for item in diagnostics[:20]:

        if item["status"] == "OK":

            st.write(
                f"Страница {item['page']} | "
                f"Заказ: `{item['order']}` | "
                f"Арт: `{item['article']}` | "
                f"Товар: `{item['product']}` | "
                f"Кол-во: `{item['qty']}` | "
                f"Этикетка: `{item['label']}` | "
                f"Метод: `{item['method']}`"
            )

        else:

            st.error(
                f"Страница {item['page']} — "
                f"НЕ НАЙДЕНО"
            )

    # ========================================================
    # DOWNLOAD
    # ========================================================

    st.download_button(
        label="⬇️ Скачать готовый PDF",
        data=output,
        file_name="Ozon_Этикетки_с_данными.pdf",
        mime="application/pdf",
        use_container_width=True
    )

