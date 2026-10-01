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

FONT_URL = (
    "https://cdnjs.cloudflare.com/ajax/libs/roboto/"
    "2.138/fonts/ttf/Roboto-Regular.ttf"
)


# ============================================================
# ШРИФТ
# ============================================================

def ensure_font():
    if os.path.exists(FONT_PATH):
        return FONT_PATH

    try:
        urllib.request.urlretrieve(
            FONT_URL,
            FONT_PATH
        )
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
# ОБЩИЕ ФУНКЦИИ
# ============================================================

def clean_spaces(value):
    if value is None:
        return ""

    value = str(value)

    value = value.replace("\xa0", " ")
    value = value.replace("\u200b", "")
    value = value.replace("\ufeff", "")

    value = re.sub(r"[ \t]+", " ", value)

    return value.strip()


def clean_identifier(value):
    if value is None:
        return ""

    value = str(value)

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


def get_short_code(order):
    """
    78277691-0407-1
    ->
    7691

    Для стандартного номера Ozon
    берём последние 4 цифры первой части.
    """

    if not order:
        return ""

    order = clean_identifier(order)

    if "-" in order:

        first_part = order.split("-")[0]

        digits = digits_only(
            first_part
        )

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
# ВОССТАНОВЛЕНИЕ НОМЕРА С ЭТИКЕТКИ
# ============================================================

def extract_orders_from_label(text):

    if not text:
        return []

    text = str(text)

    result = []

    # --------------------------------------------------------
    # Уже цельный номер
    # --------------------------------------------------------

    found = ORDER_PATTERN.findall(
        text
    )

    for order in found:

        if order not in result:
            result.append(order)

    # --------------------------------------------------------
    # Номер разбит:
    #
    # 78277691
    # -0407-1
    # --------------------------------------------------------

    lines = [
        clean_spaces(x)
        for x in text.splitlines()
    ]

    lines = [
        x for x in lines
        if x
    ]

    for i in range(
        len(lines) - 1
    ):

        a = re.sub(
            r"\s+",
            "",
            lines[i]
        )

        b = re.sub(
            r"\s+",
            "",
            lines[i + 1]
        )

        if re.fullmatch(
            r"\d{8,15}",
            a
        ) and re.fullmatch(
            r"-\d{4}-\d+",
            b
        ):

            order = a + b

            if order not in result:
                result.append(order)

    # --------------------------------------------------------
    # Внутри строки
    # --------------------------------------------------------

    flat = re.sub(
        r"\s+",
        "",
        text
    )

    pairs = re.findall(
        r"(\d{8,15})(-\d{4}-\d+)",
        flat
    )

    for a, b in pairs:

        order = a + b

        if order not in result:
            result.append(order)

    return result


# ============================================================
# ШТРИХКОД ii...
# ============================================================

def extract_ii_barcodes(text):

    if not text:
        return []

    result = []

    # Например:
    #
    # ii5010320 2537
    #
    # или
    #
    # ii50103202537

    matches = re.findall(
        r"\bii[\s\d]{8,30}",
        text,
        flags=re.IGNORECASE
    )

    for value in matches:

        value = clean_identifier(
            value
        )

        match = re.match(
            r"(ii\d+)",
            value,
            flags=re.IGNORECASE
        )

        if match:

            barcode = match.group(1)

            if barcode not in result:
                result.append(barcode)

    return result


# ============================================================
# ГОРИЗОНТАЛЬНЫЕ ЛИНИИ
# ============================================================

def get_horizontal_lines(page):
    """
    Ключевой механизм старой рабочей версии.

    Берём только графические горизонтальные линии.

    width > 30 — защита от мелких элементов.
    """

    lines = []

    try:

        for line in page.lines:

            x0 = float(
                line.get("x0", 0)
            )

            x1 = float(
                line.get("x1", 0)
            )

            y0 = float(
                line.get("top", 0)
            )

            y1 = float(
                line.get("bottom", y0)
            )

            width = abs(
                x1 - x0
            )

            height = abs(
                y1 - y0
            )

            # Горизонтальная линия
            if (
                width > 30
                and height < 3
            ):

                lines.append({
                    "top": y0,
                    "x0": min(x0, x1),
                    "x1": max(x0, x1),
                    "width": width
                })

    except Exception:
        pass

    # --------------------------------------------------------
    # Убираем почти одинаковые линии
    # --------------------------------------------------------

    lines.sort(
        key=lambda x: x["top"]
    )

    result = []

    for line in lines:

        if not result:

            result.append(line)
            continue

        if abs(
            line["top"]
            - result[-1]["top"]
        ) < 2:

            # Оставляем более длинную
            if line["width"] > result[-1]["width"]:
                result[-1] = line

        else:

            result.append(line)

    return result


# ============================================================
# БЛОКИ МЕЖДУ ГОРИЗОНТАЛЬНЫМИ ЛИНИЯМИ
# ============================================================

def get_horizontal_blocks(page):
    """
    Делим страницу на блоки по горизонтальным линиям.
    """

    lines = get_horizontal_lines(
        page
    )

    blocks = []

    if len(lines) < 2:
        return blocks

    for i in range(
        len(lines) - 1
    ):

        top = lines[i]["top"]
        bottom = lines[i + 1]["top"]

        if bottom - top < 10:
            continue

        blocks.append({
            "top": top,
            "bottom": bottom,
            "x0": min(
                lines[i]["x0"],
                lines[i + 1]["x0"]
            ),
            "x1": max(
                lines[i]["x1"],
                lines[i + 1]["x1"]
            )
        })

    return blocks


# ============================================================
# СЛОВА С КООРДИНАТАМИ
# ============================================================

def get_words_in_bbox(
    page,
    bbox
):

    try:

        crop = page.crop(
            bbox
        )

        words = crop.extract_words(
            x_tolerance=2,
            y_tolerance=3,
            keep_blank_chars=False,
            use_text_flow=False
        )

        return words or []

    except Exception:

        return []


def group_words_by_line(
    words
):

    if not words:
        return []

    words = sorted(
        words,
        key=lambda w: (
            float(w.get("top", 0)),
            float(w.get("x0", 0))
        )
    )

    lines = []

    for word in words:

        top = float(
            word.get("top", 0)
        )

        placed = False

        for line in lines:

            if abs(
                top - line["top"]
            ) <= 3:

                line["words"].append(
                    word
                )

                placed = True
                break

        if not placed:

            lines.append({
                "top": top,
                "words": [word]
            })

    for line in lines:

        line["words"].sort(
            key=lambda w: float(
                w.get("x0", 0)
            )
        )

        line["text"] = " ".join(
            w.get("text", "")
            for w in line["words"]
        )

    return lines


# ============================================================
# ОПРЕДЕЛЕНИЕ КОЛОНОК
# ============================================================

def detect_columns(
    page,
    block
):
    """
    Ищем вертикальные разделители таблицы.

    Если они есть — используем их.

    Если вертикальных линий нет,
    используем положения заголовков.
    """

    x_positions = []

    try:

        for line in page.lines:

            x0 = float(
                line.get("x0", 0)
            )

            x1 = float(
                line.get("x1", 0)
            )

            y0 = float(
                line.get("top", 0)
            )

            y1 = float(
                line.get("bottom", y0)
            )

            height = abs(
                y1 - y0
            )

            width = abs(
                x1 - x0
            )

            # Вертикальная линия
            if (
                height > 20
                and width < 3
            ):

                if (
                    y0 <= block["bottom"]
                    and y1 >= block["top"]
                ):

                    x_positions.append(
                        x0
                    )

    except Exception:
        pass

    x_positions = sorted(
        set(
            round(x, 1)
            for x in x_positions
        )
    )

    return x_positions


# ============================================================
# ТЕКСТ БЛОКА
# ============================================================

def extract_block_text(
    page,
    block
):

    bbox = (
        block["x0"],
        block["top"] + 1,
        block["x1"],
        block["bottom"] - 1
    )

    try:

        crop = page.within_bbox(
            bbox
        )

        text = crop.extract_text(
            layout=True
        )

        return text or ""

    except Exception:

        return ""


# ============================================================
# ПОИСК ЧЕТЫРЕХЗНАЧНОЙ ЭТИКЕТКИ
# ============================================================

def extract_label_code(
    text
):

    if not text:
        return ""

    # Сначала ищем явный 4-значный код
    # рядом с Этикетка.

    lines = [
        clean_spaces(x)
        for x in text.splitlines()
    ]

    for i, line in enumerate(lines):

        normalized = line.lower()

        if "этикет" in normalized:

            # В той же строке
            m = re.search(
                r"(?<!\d)(\d{4})(?!\d)",
                line
            )

            if m:
                return m.group(1)

            # Следующие строки
            for next_line in lines[
                i + 1:i + 4
            ]:

                m = re.search(
                    r"(?<!\d)(\d{4})(?!\d)",
                    next_line
                )

                if m:
                    return m.group(1)

    # Второй вариант:
    # берём все 4-значные числа
    # и последнее.

    codes = re.findall(
        r"(?<!\d)(\d{4})(?!\d)",
        text
    )

    if codes:
        return codes[-1]

    return ""


# ============================================================
# АРТИКУЛ
# ============================================================

def extract_article_from_column(
    page,
    block
):
    """
    Артикул берём только из области колонки Артикул.

    Не ищем случайные слова внутри товара.
    """

    text = extract_block_text(
        page,
        block
    )

    if not text:
        return ""

    lines = [
        clean_spaces(x)
        for x in text.splitlines()
    ]

    # --------------------------------------------------------
    # Сначала ищем явный маркер
    # --------------------------------------------------------

    for i, line in enumerate(lines):

        if re.fullmatch(
            r"Артикул",
            line,
            flags=re.IGNORECASE
        ):

            # Значение после заголовка
            for next_line in lines[
                i + 1:i + 4
            ]:

                candidate = clean_spaces(
                    next_line
                )

                if not candidate:
                    continue

                if re.fullmatch(
                    r"Артикул",
                    candidate,
                    flags=re.IGNORECASE
                ):
                    continue

                # Не берем название товара
                # и не берем количество
                if re.fullmatch(
                    r"\d{1,4}",
                    candidate
                ):
                    continue

                if re.fullmatch(
                    r"\d{4}",
                    candidate
                ):
                    continue

                return candidate

    return ""


# ============================================================
# КОЛИЧЕСТВО
# ============================================================

def extract_qty_from_column(
    page,
    block
):
    """
    Количество берём только как значение
    после поля Кол-во.
    """

    text = extract_block_text(
        page,
        block
    )

    if not text:
        return "1"

    lines = [
        clean_spaces(x)
        for x in text.splitlines()
    ]

    for i, line in enumerate(lines):

        if re.fullmatch(
            r"Кол\s*[-–—]?\s*во",
            line,
            flags=re.IGNORECASE
        ):

            for next_line in lines[
                i + 1:i + 5
            ]:

                candidate = clean_spaces(
                    next_line
                )

                m = re.fullmatch(
                    r"(\d{1,4})",
                    candidate
                )

                if m:
                    return m.group(1)

    return "1"


# ============================================================
# НАЗВАНИЕ ТОВАРА
# ============================================================

def extract_product_name(
    page,
    block
):
    """
    Название берём между:

    Товар

    и

    Артикул

    Но не используем его для маппинга.
    """

    text = extract_block_text(
        page,
        block
    )

    if not text:
        return ""

    lines = [
        clean_spaces(x)
        for x in text.splitlines()
    ]

    start = None
    end = None

    for i, line in enumerate(lines):

        low = line.lower()

        if (
            start is None
            and "товар" in low
        ):

            start = i + 1
            continue

        if (
            start is not None
            and re.fullmatch(
                r"Артикул",
                line,
                flags=re.IGNORECASE
            )
        ):

            end = i
            break

    if start is None:
        return ""

    if end is None:
        end = min(
            start + 5,
            len(lines)
        )

    name_parts = []

    for line in lines[
        start:end
    ]:

        line = clean_spaces(
            line
        )

        if not line:
            continue

        # Служебные заголовки не являются товаром
        if re.fullmatch(
            r"Фото",
            line,
            flags=re.IGNORECASE
        ):
            continue

        if re.fullmatch(
            r"Товар",
            line,
            flags=re.IGNORECASE
        ):
            continue

        name_parts.append(
            line
        )

    name = " ".join(
        name_parts
    )

    return clean_spaces(
        name
    )


# ============================================================
# НОМЕР ОТПРАВЛЕНИЯ ИЗ БЛОКА
# ============================================================

def extract_orders_from_block(
    page,
    block
):

    text = extract_block_text(
        page,
        block
    )

    return extract_orders_from_label(
        text
    )


# ============================================================
# ПАРСИНГ ОДНОГО БЛОКА
# ============================================================

def parse_one_assembly_block(
    page,
    block
):

    text = extract_block_text(
        page,
        block
    )

    if not text:
        return None

    orders = extract_orders_from_block(
        page,
        block
    )

    # Если это заголовок страницы,
    # пропускаем.
    if not orders:
        return None

    article = extract_article_from_column(
        page,
        block
    )

    qty = extract_qty_from_column(
        page,
        block
    )

    product = extract_product_name(
        page,
        block
    )

    label = extract_label_code(
        text
    )

    # --------------------------------------------------------
    # Короткий код от номера
    # --------------------------------------------------------

    if not label and orders:

        label = get_short_code(
            orders[0]
        )

    # --------------------------------------------------------
    # Идентификаторы
    # --------------------------------------------------------

    identifiers = set()

    for order in orders:

        cleaned = clean_identifier(
            order
        )

        if cleaned:
            identifiers.add(
                cleaned
            )

        numeric = digits_only(
            order
        )

        if numeric:
            identifiers.add(
                numeric
            )

        short = get_short_code(
            order
        )

        if short:
            identifiers.add(
                short
            )

    # --------------------------------------------------------
    # Четырехзначная этикетка
    # --------------------------------------------------------

    if label:

        identifiers.add(
            clean_identifier(label)
        )

    record = {
        "orders": orders,
        "article": article,
        "qty": qty,
        "product": product,
        "label": label,
        "identifiers": identifiers,
        "raw_text": text,
    }

    return record


# ============================================================
# ПАРСИНГ ЛИСТА ПОДБОРА
# ============================================================

def parse_assembly_pdf(
    pdf_bytes
):

    records = []

    with pdfplumber.open(
        io.BytesIO(pdf_bytes)
    ) as pdf:

        for page_number, page in enumerate(
            pdf.pages,
            start=1
        ):

            blocks = get_horizontal_blocks(
                page
            )

            page_records = []

            for block in blocks:

                record = parse_one_assembly_block(
                    page,
                    block
                )

                if record:

                    page_records.append(
                        record
                    )

            # ------------------------------------------------
            # Защита от дублей
            # ------------------------------------------------

            seen = set()

            for record in page_records:

                key = (
                    tuple(
                        record["orders"]
                    ),
                    record["label"],
                    record["article"]
                )

                if key in seen:
                    continue

                seen.add(key)

                record["page"] = page_number

                records.append(
                    record
                )

    # ========================================================
    # СЛОВАРИ
    # ========================================================

    identifier_map = defaultdict(list)

    label_map = defaultdict(list)

    for record in records:

        # ----------------------------------------------------
        # Все идентификаторы
        # ----------------------------------------------------

        for identifier in record[
            "identifiers"
        ]:

            identifier = clean_identifier(
                identifier
            )

            if identifier:

                identifier_map[
                    identifier
                ].append(
                    record
                )

        # ----------------------------------------------------
        # Этикетка
        # ----------------------------------------------------

        label = clean_identifier(
            record.get(
                "label",
                ""
            )
        )

        if label:

            label_map[
                label
            ].append(
                record
            )

    return (
        records,
        identifier_map,
        label_map
    )


# ============================================================
# УНИКАЛЬНАЯ ЗАПИСЬ
# ============================================================

def unique_record(
    records
):

    if not records:
        return None

    unique = []

    seen = set()

    for record in records:

        key = (
            tuple(
                record.get(
                    "orders",
                    []
                )
            ),
            record.get(
                "label",
                ""
            ),
            record.get(
                "article",
                ""
            )
        )

        if key in seen:
            continue

        seen.add(key)

        unique.append(
            record
        )

    if len(unique) == 1:
        return unique[0]

    return None


# ============================================================
# МАППИНГ ЭТИКЕТКИ
# ============================================================

def find_record_for_label(
    label_text,
    identifier_map,
    label_map
):

    # ========================================================
    # 1. Полный номер Ozon
    # ========================================================

    orders = extract_orders_from_label(
        label_text
    )

    for order in orders:

        key = clean_identifier(
            order
        )

        candidates = identifier_map.get(
            key,
            []
        )

        record = unique_record(
            candidates
        )

        if record:

            return (
                record,
                "полный номер"
            )

        # numeric
        numeric = digits_only(
            order
        )

        candidates = identifier_map.get(
            numeric,
            []
        )

        record = unique_record(
            candidates
        )

        if record:

            return (
                record,
                "numeric"
            )

    # ========================================================
    # 2. ii barcode
    # ========================================================

    barcodes = extract_ii_barcodes(
        label_text
    )

    for barcode in barcodes:

        barcode_clean = clean_identifier(
            barcode
        )

        candidates = identifier_map.get(
            barcode_clean,
            []
        )

        record = unique_record(
            candidates
        )

        if record:

            return (
                record,
                "ii barcode"
            )

        numeric = digits_only(
            barcode
        )

        candidates = identifier_map.get(
            numeric,
            []
        )

        record = unique_record(
            candidates
        )

        if record:

            return (
                record,
                "ii numeric"
            )

        # Последние 4 цифры
        last4 = get_last4(
            barcode
        )

        if last4:

            candidates = label_map.get(
                last4,
                []
            )

            record = unique_record(
                candidates
            )

            if record:

                return (
                    record,
                    "4 цифры barcode"
                )

    # ========================================================
    # 3. Последние 4 цифры любого номера
    # ========================================================

    codes = re.findall(
        r"(?<!\d)(\d{4})(?!\d)",
        label_text
    )

    codes = list(
        dict.fromkeys(codes)
    )

    for code in codes:

        candidates = label_map.get(
            code,
            []
        )

        record = unique_record(
            candidates
        )

        if record:

            return (
                record,
                "4 цифры"
            )

    return (
        None,
        "не найдено"
    )


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

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=(
            width,
            height
        )
    )

    margin = 20

    y = height - margin

    c.setFont(
        PDF_FONT,
        12
    )

    # --------------------------------------------------------
    # Заказ
    # --------------------------------------------------------

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

    product_short = clean_spaces(
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
# ДИАГНОСТИКА ЛИСТА ПОДБОРА
# ============================================================

def make_assembly_diagnostics(
    records
):

    result = []

    for index, record in enumerate(
        records,
        start=1
    ):

        result.append({
            "№": index,
            "Страница": record.get(
                "page",
                ""
            ),
            "Заказ": (
                record["orders"][0]
                if record["orders"]
                else ""
            ),
            "Артикул": record.get(
                "article",
                ""
            ),
            "Товар": clean_spaces(
                record.get(
                    "product",
                    ""
                )
            )[:20],
            "Кол-во": record.get(
                "qty",
                ""
            ),
            "Этикетка": record.get(
                "label",
                ""
            )
        })

    return result


# ============================================================
# ОБРАБОТКА
# ============================================================

def process_files(
    labels_bytes,
    assembly_bytes
):

    # --------------------------------------------------------
    # Лист подбора
    # --------------------------------------------------------

    (
        records,
        identifier_map,
        label_map
    ) = parse_assembly_pdf(
        assembly_bytes
    )

    # --------------------------------------------------------
    # PDF этикеток
    # --------------------------------------------------------

    label_reader = PdfReader(
        io.BytesIO(
            labels_bytes
        )
    )

    writer = PdfWriter()

    matched = 0
    not_found = 0

    methods = defaultdict(int)

    diagnostics = []

    # --------------------------------------------------------
    # Читаем текст этикеток
    # --------------------------------------------------------

    with pdfplumber.open(
        io.BytesIO(
            labels_bytes
        )
    ) as labels_pdf:

        for page_index, page in enumerate(
            labels_pdf.pages
        ):

            text = page.extract_text(
                x_tolerance=2,
                y_tolerance=3,
                layout=True
            ) or ""

            # ------------------------------------------------
            # Маппинг
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

            width = float(
                original_page.mediabox.width
            )

            height = float(
                original_page.mediabox.height
            )

            # ------------------------------------------------
            # Найдено
            # ------------------------------------------------

            if record:

                matched += 1

                methods[
                    method
                ] += 1

                order = (
                    record["orders"][0]
                    if record["orders"]
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
                    io.BytesIO(
                        info_pdf
                    )
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
                    "product": clean_spaces(
                        record.get(
                            "product",
                            ""
                        )
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
            # Не найдено
            # ------------------------------------------------

            else:

                not_found += 1

                info_pdf = create_info_page(
                    width=width,
                    height=height,
                    order="НЕ НАЙДЕН",
                    article="",
                    product="",
                    qty="1"
                )

                info_reader = PdfReader(
                    io.BytesIO(
                        info_pdf
                    )
                )

                writer.add_page(
                    info_reader.pages[0]
                )

                # ------------------------------------------------
                # Что реально прочитал PDF
                # ------------------------------------------------

                found_orders = extract_orders_from_label(
                    text
                )

                found_barcodes = extract_ii_barcodes(
                    text
                )

                found_codes = re.findall(
                    r"(?<!\d)(\d{4})(?!\d)",
                    text
                )

                diagnostics.append({
                    "page": page_index + 1,
                    "status": "NOT FOUND",
                    "method": method,
                    "order": (
                        found_orders[0]
                        if found_orders
                        else ""
                    ),
                    "article": "",
                    "product": "",
                    "qty": "",
                    "label": (
                        found_codes[-1]
                        if found_codes
                        else ""
                    ),
                    "barcodes": ", ".join(
                        found_barcodes
                    )
                })

    # --------------------------------------------------------
    # Результат
    # --------------------------------------------------------

    output = io.BytesIO()

    writer.write(
        output
    )

    output.seek(0)

    return (
        output.getvalue(),
        records,
        matched,
        not_found,
        methods,
        diagnostics
    )


# ============================================================
# STREAMLIT
# ============================================================

st.title(
    "🖨️ Склейка: Этикетки + Лист подбора"
)

st.caption(
    "Маппинг построен по графическим горизонтальным "
    "линиям листа подбора. Фото товара не участвует "
    "в определении артикула и количества."
)

col1, col2 = st.columns(2)

with col1:

    labels_file = st.file_uploader(
        "📦 Этикетки Ozon",
        type=["pdf"],
        key="labels_pdf"
    )

with col2:

    assembly_file = st.file_uploader(
        "📋 Лист подбора Ozon",
        type=["pdf"],
        key="assembly_pdf"
    )


# ============================================================
# КНОПКА
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
        "Разбираю графические блоки листа подбора..."
    ):

        try:

            (
                output,
                records,
                matched,
                not_found,
                methods,
                diagnostics
            ) = process_files(
                labels_file.getvalue(),
                assembly_file.getvalue()
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

    c1, c2, c3 = st.columns(3)

    with c1:

        st.metric(
            "Записей листа подбора",
            len(records)
        )

    with c2:

        st.metric(
            "Этикеток найдено",
            matched
        )

    with c3:

        st.metric(
            "Не найдено",
            not_found
        )

    # ========================================================
    # ДИАГНОСТИКА ЛИСТА ПОДБОРА
    # ========================================================

    st.subheader(
        "📋 Что извлечено из листа подбора"
    )

    assembly_diag = make_assembly_diagnostics(
        records
    )

    if assembly_diag:

        st.dataframe(
            assembly_diag[:100],
            use_container_width=True,
            hide_index=True
        )

    else:

        st.warning(
            "Из листа подбора не удалось извлечь "
            "ни одной записи между горизонтальными линиями."
        )

    # ========================================================
    # МЕТОДЫ МАППИНГА
    # ========================================================

    if methods:

        st.subheader(
            "🔗 Маппинг этикеток"
        )

        for method, count in methods.items():

            st.write(
                f"**{method}:** {count}"
            )

    # ========================================================
    # РЕЗУЛЬТАТЫ ЭТИКЕТОК
    # ========================================================

    st.subheader(
        "🔎 Результаты"
    )

    for item in diagnostics[:30]:

        if item["status"] == "OK":

            st.write(
                f"Страница **{item['page']}** | "
                f"Заказ: `{item['order']}` | "
                f"Арт: `{item['article'] or '-'}` | "
                f"Товар: `{item['product'] or '-'}` | "
                f"Кол-во: `{item['qty']}` | "
                f"Этикетка: `{item['label']}` | "
                f"Метод: `{item['method']}`"
            )

        else:

            extra = ""

            if item.get("barcodes"):
                extra = (
                    f" | barcode: "
                    f"`{item['barcodes']}`"
                )

            st.error(
                f"Страница **{item['page']}** — "
                f"НЕ НАЙДЕНО | "
                f"Заказ: `{item['order'] or '-'}` | "
                f"4 цифры: `{item['label'] or '-'}`"
                f"{extra}"
            )

    # ========================================================
    # СКАЧИВАНИЕ
    # ========================================================

    st.download_button(
        label="⬇️ Скачать готовый PDF",
        data=output,
        file_name="Ozon_Этикетки_с_данными.pdf",
        mime="application/pdf",
        use_container_width=True
    )

