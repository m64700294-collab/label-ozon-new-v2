import io
import os
import re
import requests
import streamlit as st

import pdfplumber

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# НАСТРОЙКИ
# ============================================================

FONT_NAME = "OzonFont"
FONT_FILE = "Roboto_Full_Final.ttf"

FONT_URL = (
    "https://github.com/googlefonts/roboto/raw/main/"
    "src/hinted/Roboto-Regular.ttf"
)


# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(
    page_title="Ozon — Этикетки",
    page_icon="🖨️",
    layout="centered"
)

st.title("🖨️ Склейка: Этикетки + Лист подбора")

st.caption(
    "К каждой этикетке будет добавлен лист с товаром, "
    "артикулом и количеством."
)


# ============================================================
# ШРИФТ
# ============================================================

@st.cache_resource
def load_font():

    try:
        pdfmetrics.getFont(FONT_NAME)
        return FONT_NAME
    except Exception:
        pass

    font_path = None

    candidates = [
        FONT_FILE,
        os.path.join(os.getcwd(), FONT_FILE),
    ]

    try:
        candidates.append(
            os.path.join(
                os.path.dirname(__file__),
                FONT_FILE
            )
        )
    except Exception:
        pass

    for path in candidates:
        if path and os.path.exists(path):
            font_path = path
            break

    if not font_path:

        try:

            response = requests.get(
                FONT_URL,
                timeout=30
            )

            response.raise_for_status()

            font_path = FONT_FILE

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

            st.stop()

    try:

        pdfmetrics.registerFont(
            TTFont(
                FONT_NAME,
                font_path
            )
        )

    except Exception as e:

        st.error(
            f"Не удалось зарегистрировать шрифт: {e}"
        )

        st.stop()

    return FONT_NAME


FONT = load_font()


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================

def normalize_spaces(text):

    if not text:
        return ""

    text = str(text)

    text = text.replace(
        "\xa0",
        " "
    )

    text = text.replace(
        "\r",
        "\n"
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    text = re.sub(
        r"\n+",
        "\n",
        text
    )

    return text.strip()


def flat_text(text):

    if not text:
        return ""

    text = text.replace(
        "\xa0",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# НОМЕРА ОТПРАВЛЕНИЙ
# ============================================================

# Основной формат Ozon:
#
# 78277691-0407-1
#
# Также допускаем варианты без дефисов.
ORDER_PATTERN = re.compile(
    r"""
    (?:
        \d{6,20}-\d{2,10}-\d{1,10}
        |
        \d{10,25}
    )
    """,
    re.IGNORECASE | re.VERBOSE
)


def normalize_order(value):

    if not value:
        return ""

    value = str(value)

    value = value.replace(
        "\xa0",
        " "
    )

    value = re.sub(
        r"\s+",
        "",
        value
    )

    return value.upper()


def get_numeric_key(value):

    if not value:
        return ""

    return re.sub(
        r"\D",
        "",
        str(value)
    )


def get_last10_key(value):

    digits = get_numeric_key(
        value
    )

    if len(digits) >= 10:
        return digits[-10:]

    return digits


def get_short_code(value):

    """
    Для:

        78277691-0407-1

    получаем:

        7691

    ВАЖНО:
    это последние 4 цифры первой части номера.
    """

    value = normalize_order(
        value
    )

    if not value:
        return ""

    parts = value.split("-")

    if parts:

        first = re.sub(
            r"\D",
            "",
            parts[0]
        )

        if len(first) >= 4:
            return first[-4:]

    digits = get_numeric_key(
        value
    )

    if len(digits) >= 4:
        return digits[-4:]

    return digits


def find_orders(text):

    if not text:
        return []

    result = []

    for match in ORDER_PATTERN.findall(
        text
    ):

        order = normalize_order(
            match
        )

        if order and order not in result:
            result.append(
                order
            )

    return result


# ============================================================
# УДАЛЕНИЕ НОМЕРОВ ИЗ БЛОКА
# ============================================================

def remove_orders(
    text,
    orders
):

    result = text or ""

    for order in orders or []:

        if not order:
            continue

        result = re.sub(
            re.escape(order),
            " ",
            result,
            flags=re.IGNORECASE
        )

    return result


# ============================================================
# НОРМАЛИЗАЦИЯ ЗАГОЛОВКОВ
# ============================================================

def normalize_headers(text):

    if not text:
        return ""

    text = text.replace(
        "\xa0",
        " "
    )

    # Фото Товар -> Товар
    text = re.sub(
        r"Фото\s+Товар",
        "Товар",
        text,
        flags=re.IGNORECASE
    )

    # Возможный перенос:
    # Фото
    # Товар
    text = re.sub(
        r"Фото\s+Товар",
        "Товар",
        text,
        flags=re.IGNORECASE
    )

    # Кол во
    # Кол-во
    # Кол – во
    text = re.sub(
        r"Кол\s*[-–—]?\s*во",
        "Кол-во",
        text,
        flags=re.IGNORECASE
    )

    # Номер с этикетки
    text = re.sub(
        r"Номер\s+с\s+этикетки",
        "Номер с этикетки",
        text,
        flags=re.IGNORECASE
    )

    return text


# ============================================================
# ИЗВЛЕЧЕНИЕ МЕЖДУ ПОЛЯМИ
# ============================================================

def extract_between(
    text,
    start,
    end
):

    if not text:
        return ""

    pattern = (
        start
        + r"\s*(.*?)\s*"
        + end
    )

    match = re.search(
        pattern,
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    if not match:
        return ""

    value = match.group(1)

    value = flat_text(
        value
    )

    return value.strip(
        " -–—:"
    )


# ============================================================
# ПАРСИНГ ТОВАРНОГО БЛОКА
# ============================================================

def parse_product_block(
    text,
    orders
):

    result = {
        "name": "Товар",
        "article": "-",
        "qty": 1
    }

    if not text:
        return result

    # --------------------------------------------------------
    # Подготовка
    # --------------------------------------------------------

    text = normalize_headers(
        text
    )

    # Важно: удаляем номера отправлений,
    # чтобы они не попали в товар/артикул.
    clean = remove_orders(
        text,
        orders
    )

    flat = flat_text(
        clean
    )

    if not flat:
        return result

    # --------------------------------------------------------
    # ТОВАР
    # --------------------------------------------------------

    name = extract_between(
        flat,
        r"\bТовар\b",
        r"\bАртикул\b"
    )

    # --------------------------------------------------------
    # АРТИКУЛ
    # --------------------------------------------------------

    article = extract_between(
        flat,
        r"\bАртикул\b",
        r"\bКол-во\b"
    )

    # --------------------------------------------------------
    # КОЛИЧЕСТВО
    #
    # КРИТИЧЕСКИ ВАЖНО:
    #
    # ищем число только между:
    #
    # Кол-во
    #
    # и
    #
    # Этикетка
    #
    # Поэтому 7691 ниже не попадёт.
    # --------------------------------------------------------

    qty_text = extract_between(
        flat,
        r"\bКол-во\b",
        r"\bЭтикетка\b"
    )

    qty = 1

    if qty_text:

        qty_match = re.search(
            r"(?<!\d)(\d{1,3})(?!\d)",
            qty_text
        )

        if qty_match:

            try:

                value = int(
                    qty_match.group(1)
                )

                if value > 0:
                    qty = value

            except Exception:
                qty = 1

    # --------------------------------------------------------
    # Дополнительный fallback для товара
    # --------------------------------------------------------

    if not name:

        match = re.search(
            r"\bТовар\b\s*(.+?)\s+\bАртикул\b",
            flat,
            flags=re.IGNORECASE
        )

        if match:

            name = flat_text(
                match.group(1)
            )

    # --------------------------------------------------------
    # Дополнительный fallback для артикула
    # --------------------------------------------------------

    if not article:

        match = re.search(
            r"\bАртикул\b\s*"
            r"(.+?)"
            r"\s+\bКол-во\b",
            flat,
            flags=re.IGNORECASE
        )

        if match:

            article = flat_text(
                match.group(1)
            )

    # --------------------------------------------------------
    # Чистим
    # --------------------------------------------------------

    if name:

        name = name.strip(
            " -–—:"
        )

    if article:

        article = article.strip(
            " -–—:"
        )

    # --------------------------------------------------------
    # Если артикул содержит несколько пробелов —
    # приводим к нормальному виду.
    # --------------------------------------------------------

    article = flat_text(
        article
    )

    name = flat_text(
        name
    )

    # --------------------------------------------------------
    # Защита от случайного мусора
    # --------------------------------------------------------

    if not name:
        name = "Товар"

    if not article:
        article = "-"

    if not qty or qty < 1:
        qty = 1

    result["name"] = name
    result["article"] = article
    result["qty"] = qty

    return result


# ============================================================
# ГОРИЗОНТАЛЬНЫЕ ЛИНИИ
# ============================================================

def get_horizontal_lines(
    page
):

    result = []

    try:
        lines = page.lines or []
    except Exception:
        lines = []

    for line in lines:

        try:

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

            width = abs(
                x1 - x0
            )

            height = abs(
                y1 - y0
            )

            if (
                width >= 30
                and height <= 3
            ):

                result.append(
                    y0
                )

        except Exception:
            continue

    result.sort()

    # Убираем почти одинаковые
    # координаты линий.
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
# ДОБАВЛЕНИЕ В ИНДЕКС
# ============================================================

def add_item_to_index(
    index,
    item
):

    order = item.get(
        "order",
        ""
    )

    # --------------------------------------------------------
    # Полный номер
    # --------------------------------------------------------

    normalized = normalize_order(
        order
    )

    if normalized:

        index[
            "ORDER_" + normalized
        ] = item

    # --------------------------------------------------------
    # Numeric
    # --------------------------------------------------------

    numeric = get_numeric_key(
        order
    )

    if numeric:

        index[
            "NUM_" + numeric
        ] = item

    # --------------------------------------------------------
    # Last 10
    # --------------------------------------------------------

    last10 = get_last10_key(
        order
    )

    if last10:

        index[
            "LAST10_" + last10
        ] = item

    # --------------------------------------------------------
    # Короткий код
    # --------------------------------------------------------

    short = get_short_code(
        order
    )

    if short:

        # Не перезаписываем хороший товар
        # пустым/неполным.
        old = index.get(
            "SHORT_" + short
        )

        if old is None:

            index[
                "SHORT_" + short
            ] = item

        else:

            old_article = old.get(
                "article",
                "-"
            )

            new_article = item.get(
                "article",
                "-"
            )

            if (
                old_article == "-"
                and new_article != "-"
            ):

                index[
                    "SHORT_" + short
                ] = item


# ============================================================
# ПАРСИНГ ЛИСТА ПОДБОРА
# ============================================================

def parse_assembly_list(
    pdf_file
):

    index = {}

    try:
        pdf_file.seek(0)

        pdf_bytes = pdf_file.read()

        if not pdf_bytes:

            st.error(
                "PDF листа подбора пустой."
            )

            return index

        with pdfplumber.open(
            io.BytesIO(pdf_bytes)
        ) as pdf:

            total_pages = len(
                pdf.pages
            )

            progress = st.progress(
                0,
                text="Читаем лист подбора..."
            )

            for page_number, page in enumerate(
                pdf.pages,
                start=1
            ):

                try:

                    y_lines = get_horizontal_lines(
                        page
                    )

                    # =================================================
                    # ВАРИАНТ 1:
                    # Есть горизонтальные линии.
                    # =================================================

                    if len(y_lines) >= 2:

                        for i in range(
                            len(y_lines) - 1
                        ):

                            top = y_lines[i]
                            bottom = y_lines[i + 1]

                            if (
                                bottom - top
                                < 20
                            ):
                                continue

                            try:

                                cropped = page.crop(
                                    (
                                        0,
                                        top,
                                        page.width,
                                        bottom
                                    )
                                )

                            except Exception:
                                continue

                            # -----------------------------------------
                            # Основной текст
                            # -----------------------------------------

                            try:

                                text = (
                                    cropped.extract_text(
                                        layout=False
                                    )
                                    or ""
                                )

                            except Exception:

                                text = ""

                            # -----------------------------------------
                            # Если normal пустой — layout
                            # -----------------------------------------

                            if not text.strip():

                                try:

                                    text = (
                                        cropped.extract_text(
                                            layout=True
                                        )
                                        or ""
                                    )

                                except Exception:

                                    text = ""

                            if not text.strip():
                                continue

                            # -----------------------------------------
                            # Номер отправления
                            # -----------------------------------------

                            orders = find_orders(
                                text
                            )

                            # -----------------------------------------
                            # Если не нашли —
                            # пробуем layout отдельно.
                            # -----------------------------------------

                            if not orders:

                                try:

                                    layout_text = (
                                        cropped.extract_text(
                                            layout=True
                                        )
                                        or ""
                                    )

                                except Exception:

                                    layout_text = ""

                                orders = find_orders(
                                    layout_text
                                )

                                if orders:
                                    text = layout_text

                            if not orders:
                                continue

                            # -----------------------------------------
                            # Товар
                            # -----------------------------------------

                            data = parse_product_block(
                                text,
                                orders
                            )

                            # -----------------------------------------
                            # Индекс
                            # -----------------------------------------

                            for order in orders:

                                item = {
                                    "order": order,
                                    "name": data["name"],
                                    "article": data["article"],
                                    "qty": data["qty"]
                                }

                                add_item_to_index(
                                    index,
                                    item
                                )

                    # =================================================
                    # ВАРИАНТ 2:
                    # Нет горизонтальных линий.
                    # Читаем страницу целиком.
                    # =================================================

                    else:

                        try:

                            text = (
                                page.extract_text(
                                    layout=False
                                )
                                or ""
                            )

                        except Exception:

                            text = ""

                        if not text.strip():

                            try:

                                text = (
                                    page.extract_text(
                                        layout=True
                                    )
                                    or ""
                                )

                            except Exception:

                                text = ""

                        orders = find_orders(
                            text
                        )

                        if orders:

                            data = parse_product_block(
                                text,
                                orders
                            )

                            for order in orders:

                                item = {
                                    "order": order,
                                    "name": data["name"],
                                    "article": data["article"],
                                    "qty": data["qty"]
                                }

                                add_item_to_index(
                                    index,
                                    item
                                )

                except Exception as e:

                    st.warning(
                        f"Ошибка страницы "
                        f"{page_number}: {e}"
                    )

                progress.progress(
                    page_number / max(
                        total_pages,
                        1
                    ),
                    text=(
                        f"Лист подбора: "
                        f"{page_number}/"
                        f"{total_pages}"
                    )
                )

            progress.empty()

    except Exception as e:

        st.error(
            "Ошибка при чтении листа подбора."
        )

        st.exception(e)

    return index


# ============================================================
# ПОИСК ТОВАРА ПО ЭТИКЕТКЕ
# ============================================================

def find_item_for_label(
    order,
    short_code,
    index
):

    # ========================================================
    # 1. Полный номер
    # ========================================================

    normalized = normalize_order(
        order
    )

    if normalized:

        item = index.get(
            "ORDER_" + normalized
        )

        if item:
            return item

    # ========================================================
    # 2. Цифры полного номера
    # ========================================================

    numeric = get_numeric_key(
        order
    )

    if numeric:

        item = index.get(
            "NUM_" + numeric
        )

        if item:
            return item

    # ========================================================
    # 3. Последние 10 цифр
    # ========================================================

    last10 = get_last10_key(
        order
    )

    if last10:

        item = index.get(
            "LAST10_" + last10
        )

        if item:
            return item

    # ========================================================
    # 4. Короткий код с этикетки
    # ========================================================

    if short_code:

        short_code = re.sub(
            r"\D",
            "",
            str(short_code)
        )

        if short_code:

            item = index.get(
                "SHORT_" + short_code
            )

            if item:
                return item

    return None


# ============================================================
# ИЗВЛЕЧЕНИЕ ДАННЫХ С ЭТИКЕТКИ
# ============================================================

def extract_label_identifiers(
    page
):

    texts = []

    # --------------------------------------------------------
    # Обычный текст
    # --------------------------------------------------------

    try:

        text = page.extract_text(
            layout=False
        ) or ""

        if text:
            texts.append(
                text
            )

    except Exception:
        pass

    # --------------------------------------------------------
    # Layout
    # --------------------------------------------------------

    try:

        text = page.extract_text(
            layout=True
        ) or ""

        if text:
            texts.append(
                text
            )

    except Exception:
        pass

    # --------------------------------------------------------
    # Собираем все номера
    # --------------------------------------------------------

    all_orders = []

    for text in texts:

        orders = find_orders(
            text
        )

        for order in orders:

            if order not in all_orders:

                all_orders.append(
                    order
                )

    # --------------------------------------------------------
    # Основной номер
    # --------------------------------------------------------

    order = ""

    if all_orders:

        # Сначала номер с дефисами
        for candidate in all_orders:

            if "-" in candidate:

                order = candidate
                break

        if not order:
            order = all_orders[0]

    # --------------------------------------------------------
    # Короткий код
    # --------------------------------------------------------

    short_code = ""

    if order:

        short_code = get_short_code(
            order
        )

    # --------------------------------------------------------
    # Если полного номера нет,
    # ищем отдельный 4-значный код.
    #
    # Это важно для Ozon этикеток.
    # --------------------------------------------------------

    if not short_code:

        candidates = []

        for text in texts:

            # Все отдельные 4-значные числа
            found = re.findall(
                r"(?<!\d)(\d{4})(?!\d)",
                text
            )

            for value in found:

                if value not in candidates:
                    candidates.append(
                        value
                    )

        # Предпочитаем последние найденные.
        # В типичной этикетке код расположен
        # ближе к номеру отправления.
        if candidates:

            short_code = candidates[-1]

    return {
        "order": order,
        "short_code": short_code,
        "texts": texts
    }


# ============================================================
# ПЕРЕНОС ТЕКСТА
# ============================================================

def wrap_text(
    text,
    max_chars
):

    if not text:
        return [""]

    words = str(
        text
    ).split()

    lines = []

    current = ""

    for word in words:

        if not current:

            current = word

        elif (
            len(current)
            + len(word)
            + 1
            <= max_chars
        ):

            current += (
                " " + word
            )

        else:

            lines.append(
                current
            )

            current = word

    if current:
        lines.append(
            current
        )

    return lines


# ============================================================
# ИНФОРМАЦИОННАЯ СТРАНИЦА
# ============================================================

def create_info_label(
    order,
    article,
    name,
    qty
):

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    width, height = A4

    # ========================================================
    # Заголовок
    # ========================================================

    c.setFont(
        FONT,
        24
    )

    c.drawString(
        50,
        height - 70,
        "ЛИСТ ПОДБОРА"
    )

    # ========================================================
    # Номер отправления
    # ========================================================

    c.setFont(
        FONT,
        14
    )

    c.drawString(
        50,
        height - 115,
        "Номер отправления:"
    )

    c.setFont(
        FONT,
        22
    )

    c.drawString(
        50,
        height - 145,
        str(
            order or "-"
        )
    )

    # ========================================================
    # Артикул
    # ========================================================

    c.setFont(
        FONT,
        14
    )

    c.drawString(
        50,
        height - 195,
        "Артикул:"
    )

    c.setFont(
        FONT,
        28
    )

    c.drawString(
        50,
        height - 235,
        str(
            article or "-"
        )
    )

    # ========================================================
    # Количество
    # ========================================================

    c.setFont(
        FONT,
        14
    )

    c.drawString(
        50,
        height - 285,
        "Количество:"
    )

    c.setFont(
        FONT,
        32
    )

    c.drawString(
        50,
        height - 330,
        str(
            qty or 1
        )
    )

    # ========================================================
    # Товар
    # ========================================================

    c.setFont(
        FONT,
        14
    )

    c.drawString(
        50,
        height - 390,
        "Товар:"
    )

    name = name or "Товар"

    lines = wrap_text(
        name,
        42
    )

    font_size = 20

    if len(lines) > 4:
        font_size = 18

    if len(lines) > 6:
        font_size = 16

    c.setFont(
        FONT,
        font_size
    )

    y = height - 430

    for line in lines[:8]:

        c.drawString(
            50,
            y,
            line
        )

        y -= (
            font_size + 8
        )

    # ========================================================
    # Рамка
    # ========================================================

    c.setLineWidth(
        1
    )

    c.rect(
        35,
        35,
        width - 70,
        height - 70
    )

    c.save()

    buffer.seek(0)

    return buffer


# ============================================================
# ОБРАБОТКА ЭТИКЕТОК
# ============================================================

def process_labels(
    labels_file,
    index
):

    labels_file.seek(0)

    data = labels_file.read()

    reader = PdfReader(
        io.BytesIO(data)
    )

    writer = PdfWriter()

    total = len(
        reader.pages
    )

    progress = st.progress(
        0,
        text="Обрабатываем этикетки..."
    )

    found = 0
    not_found = 0

    not_found_data = []

    for page_number, page in enumerate(
        reader.pages,
        start=1
    ):

        # ====================================================
        # 1. ОРИГИНАЛЬНАЯ ЭТИКЕТКА
        #
        # НИКАК НЕ МЕНЯЕМ ЕЁ.
        # ====================================================

        writer.add_page(
            page
        )

        # ====================================================
        # 2. Извлекаем номер/код
        # ====================================================

        identifiers = extract_label_identifiers(
            page
        )

        order = identifiers[
            "order"
        ]

        short_code = identifiers[
            "short_code"
        ]

        # ====================================================
        # 3. Ищем товар
        # ====================================================

        item = find_item_for_label(
            order,
            short_code,
            index
        )

        # ====================================================
        # 4. Создаём дополнительную страницу
        # ====================================================

        if item:

            found += 1

            info_pdf = create_info_label(
                order=item.get(
                    "order",
                    order or "-"
                ),
                article=item.get(
                    "article",
                    "-"
                ),
                name=item.get(
                    "name",
                    "Товар"
                ),
                qty=item.get(
                    "qty",
                    1
                )
            )

        else:

            not_found += 1

            not_found_data.append({
                "page": page_number,
                "order": order,
                "short_code": short_code
            })

            info_pdf = create_info_label(
                order=order or (
                    short_code or
                    "Не найден"
                ),
                article="-",
                name="Товар не найден",
                qty=1
            )

        # ====================================================
        # 5. Добавляем инфо-страницу
        # ====================================================

        info_reader = PdfReader(
            info_pdf
        )

        writer.add_page(
            info_reader.pages[0]
        )

        progress.progress(
            page_number / max(
                total,
                1
            ),
            text=(
                f"Этикетки: "
                f"{page_number}/{total}"
            )
        )

    progress.empty()

    output = io.BytesIO()

    writer.write(
        output
    )

    output.seek(0)

    return (
        output,
        found,
        not_found,
        not_found_data
    )


# ============================================================
# ИНТЕРФЕЙС
# ============================================================

st.divider()

labels_file = st.file_uploader(
    "1️⃣ PDF с этикетками Ozon",
    type=["pdf"],
    key="labels"
)

assembly_file = st.file_uploader(
    "2️⃣ PDF с листом подбора Ozon",
    type=["pdf"],
    key="assembly"
)


# ============================================================
# ЗАПУСК
# ============================================================

if st.button(
    "🔥 Сформировать готовый PDF",
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

    # ========================================================
    # Читаем лист подбора
    # ========================================================

    with st.spinner(
        "📋 Читаем лист подбора..."
    ):

        index = parse_assembly_list(
            assembly_file
        )

    # --------------------------------------------------------
    # Считаем уникальные отправления
    # --------------------------------------------------------

    unique_orders = set()

    for key, item in index.items():

        if key.startswith(
            (
                "ORDER_",
                "NUM_",
                "LAST10_",
                "SHORT_"
            )
        ):

            order = item.get(
                "order"
            )

            if order:
                unique_orders.add(
                    order
                )

    if not unique_orders:

        st.error(
            "❌ В листе подбора не найдено ни одного отправления."
        )

        st.stop()

    st.success(
        f"📋 В листе подбора найдено "
        f"{len(unique_orders)} отправлений."
    )

    # ========================================================
    # ПРОВЕРКА РАСПОЗНАННЫХ ДАННЫХ
    # ========================================================

    with st.expander(
        "🔎 Проверить распознанные товары"
    ):

        displayed = set()

        for key, item in index.items():

            if not key.startswith(
                "SHORT_"
            ):
                continue

            order = item.get(
                "order",
                ""
            )

            if order in displayed:
                continue

            displayed.add(
                order
            )

            st.write(
                f"**{order}**  "
                f"| Код: `{get_short_code(order)}`  "
                f"| Артикул: `{item.get('article', '-')}`  "
                f"| Кол-во: `{item.get('qty', 1)}`"
            )

            st.caption(
                item.get(
                    "name",
                    "Товар"
                )
            )

    # ========================================================
    # ОБРАБАТЫВАЕМ ЭТИКЕТКИ
    # ========================================================

    with st.spinner(
        "🖨️ Склеиваем этикетки..."
    ):

        result = process_labels(
            labels_file,
            index
        )

    output = result[0]
    found = result[1]
    not_found = result[2]
    not_found_data = result[3]

    if output is None:

        st.error(
            "Не удалось сформировать PDF."
        )

        st.stop()

    # ========================================================
    # СТАТИСТИКА
    # ========================================================

    st.divider()

    col1, col2, col3 = st.columns(
        3
    )

    with col1:

        st.metric(
            "Всего этикеток",
            found + not_found
        )

    with col2:

        st.metric(
            "Найдено",
            found
        )

    with col3:

        st.metric(
            "Не найдено",
            not_found
        )

    # ========================================================
    # ЕСЛИ ЧТО-ТО НЕ НАШЛОСЬ
    # ========================================================

    if not_found_data:

        with st.expander(
            "⚠️ Что не сопоставилось"
        ):

            for row in not_found_data:

                st.write(
                    f"Страница **{row['page']}** | "
                    f"Отправление: "
                    f"`{row['order'] or '-'}` | "
                    f"Код: "
                    f"`{row['short_code'] or '-'}`"
                )

    # ========================================================
    # СКАЧИВАНИЕ
    # ========================================================

    st.success(
        f"✅ Готово. Найдено {found} из "
        f"{found + not_found} этикеток."
    )

    st.download_button(
        label="📥 Скачать Ready_Labels.pdf",
        data=output.getvalue(),
        file_name="Ready_Labels.pdf",
        mime="application/pdf",
        use_container_width=True
    )

