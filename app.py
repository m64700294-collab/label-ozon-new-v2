import streamlit as st
import re
import os
from io import BytesIO

import pandas as pd
import pdfplumber

from pypdf import PdfReader, PdfWriter

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import mm


# ============================================================
# НАСТРОЙКИ
# ============================================================

st.set_page_config(
    page_title="Ozon FBS — Этикетки",
    page_icon="📦",
    layout="wide"
)

# Информационная наклейка Ozon
LABEL_WIDTH = 58 * mm
LABEL_HEIGHT = 40 * mm

# Размер шрифта
INFO_FONT_SIZE = 6.5
INFO_TITLE_SIZE = 7.5

# Максимальное количество строк в информационной наклейке
MAX_TITLE_LINES = 3

# ============================================================
# ШРИФТ ROBOTO
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def register_fonts():
    """
    Регистрируем только Roboto / OzonFont.
    DejaVu специально не используем.
    """

    candidates_regular = [
        os.path.join(BASE_DIR, "Roboto_Full_Final.ttf"),
        os.path.join(BASE_DIR, "Roboto-Regular.ttf"),
        os.path.join(BASE_DIR, "OzonFont_Fix.ttf"),
    ]

    candidates_bold = [
        os.path.join(BASE_DIR, "Roboto_Full_Final.ttf"),
        os.path.join(BASE_DIR, "OzonFont_Fix.ttf"),
    ]

    regular_path = None
    bold_path = None

    for path in candidates_regular:
        if os.path.exists(path):
            regular_path = path
            break

    for path in candidates_bold:
        if os.path.exists(path):
            bold_path = path
            break

    if regular_path:
        try:
            pdfmetrics.registerFont(
                TTFont("OzonRoboto", regular_path)
            )
        except Exception:
            pass

    if bold_path:
        try:
            pdfmetrics.registerFont(
                TTFont("OzonRobotoBold", bold_path)
            )
        except Exception:
            pass

    registered = pdfmetrics.getRegisteredFontNames()

    if "OzonRoboto" in registered:
        return "OzonRoboto", (
            "OzonRobotoBold"
            if "OzonRobotoBold" in registered
            else "OzonRoboto"
        )

    return "Helvetica", "Helvetica-Bold"


FONT_REGULAR, FONT_BOLD = register_fonts()


# ============================================================
# ОБЩИЕ ФУНКЦИИ
# ============================================================

def normalize_text(value):
    if value is None:
        return ""

    value = str(value)

    value = value.replace("\xa0", " ")
    value = value.replace("\u200b", "")
    value = value.replace("\ufeff", "")

    return value.strip()


def normalize_order(value):
    """
    Нормализация номера отправления.

    Важно:
    0115480687-0268-1
    остается:
    0115480687-0268-1

    Убираем пробелы, но НЕ удаляем дефисы.
    """

    value = normalize_text(value)

    value = re.sub(r"\s+", "", value)

    return value


def normalize_key(value):
    """
    Нормализация для поиска в словарях.
    """

    value = normalize_order(value)

    return value.upper()


def clean_number(value):
    """
    Оставляет только цифры.
    """

    if value is None:
        return ""

    return re.sub(r"\D", "", str(value))


def is_numeric_token(value):
    return bool(re.fullmatch(r"\d{2,10}", value or ""))


def looks_like_suffix(value):
    """
    Хвост старого номера:

    -0268-1
    -0110-2
    """

    if not value:
        return False

    return bool(
        re.fullmatch(
            r"-\d{2,8}-\d{1,4}",
            value.strip()
        )
    )


def normalize_suffix(value):
    value = normalize_text(value)

    m = re.search(
        r"-\d{2,8}-\d{1,4}",
        value
    )

    if not m:
        return ""

    return m.group(0)


# ============================================================
# ПОИСК КОЛОНОК В API XLSX/CSV
# ============================================================

def find_column(df, patterns):
    """
    Ищет колонку по нескольким возможным названиям.
    """

    normalized = {}

    for col in df.columns:
        name = normalize_text(col).lower()
        normalized[col] = name

    # сначала точное совпадение
    for pattern in patterns:
        p = pattern.lower()

        for col, name in normalized.items():
            if name == p:
                return col

    # затем вхождение
    for pattern in patterns:
        p = pattern.lower()

        for col, name in normalized.items():
            if p in name:
                return col

    return None


# ============================================================
# ЗАГРУЗКА API-ФАЙЛА
# ============================================================

def read_api_file(uploaded_file):
    """
    Загружает XLSX или CSV.
    """

    if uploaded_file is None:
        return None

    filename = uploaded_file.name.lower()

    try:

        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            df = pd.read_excel(uploaded_file)

        elif filename.endswith(".csv"):
            try:
                df = pd.read_csv(
                    uploaded_file,
                    sep=None,
                    engine="python"
                )
            except Exception:
                uploaded_file.seek(0)
                df = pd.read_csv(
                    uploaded_file,
                    sep=";"
                )

        else:
            st.error(
                "Поддерживаются только XLSX/XLS/CSV."
            )
            return None

        return df

    except Exception as e:

        st.error(
            f"Ошибка чтения API-файла: {e}"
        )

        return None


# ============================================================
# ПОСТРОЕНИЕ ИНДЕКСА API
# ============================================================

def build_api_index(df):
    """
    Создает индекс:

    номер отправления ->
    данные отправления
    """

    if df is None or df.empty:
        return {}

    order_col = find_column(
        df,
        [
            "номер отправления",
            "номер отправки",
            "номер заказа",
            "posting number",
            "posting_number",
            "posting",
            "отправление",
        ]
    )

    article_col = find_column(
        df,
        [
            "артикул",
            "артикул товара",
            "offer_id",
            "offer id",
            "sku",
        ]
    )

    name_col = find_column(
        df,
        [
            "название",
            "наименование",
            "название товара",
            "товар",
            "product name",
        ]
    )

    qty_col = find_column(
        df,
        [
            "количество",
            "кол-во",
            "колво",
            "qty",
            "quantity",
        ]
    )

    if order_col is None:

        st.error(
            "В API-файле не найдена колонка с номером отправления."
        )

        st.write(
            "Найденные колонки:",
            list(df.columns)
        )

        return {}

    result = {}

    for _, row in df.iterrows():

        raw_order = row.get(order_col, "")

        order = normalize_order(raw_order)

        if not order:
            continue

        data = {
            "order": order,
            "article": (
                normalize_text(row.get(article_col, ""))
                if article_col is not None
                else ""
            ),
            "name": (
                normalize_text(row.get(name_col, ""))
                if name_col is not None
                else ""
            ),
            "quantity": (
                normalize_text(row.get(qty_col, ""))
                if qty_col is not None
                else ""
            ),
        }

        result[normalize_key(order)] = data

    return result


def get_order_data(api_index, order):
    if not order:
        return None

    key = normalize_key(order)

    return api_index.get(key)


# ============================================================
# ИЗВЛЕЧЕНИЕ ТЕКСТА ИЗ PDF
# ============================================================

def extract_page_content(page):
    """
    Извлекаем текст несколькими способами.

    Возвращаем:
        text
        words
    """

    text = ""

    # --------------------------------------------------------
    # pdfplumber extract_text
    # --------------------------------------------------------

    try:
        text = page.extract_text(
            x_tolerance=2,
            y_tolerance=3
        ) or ""
    except Exception:
        text = ""

    # --------------------------------------------------------
    # pdfplumber extract_words
    # --------------------------------------------------------

    words = []

    try:
        words = page.extract_words(
            keep_blank_chars=False,
            use_text_flow=False
        ) or []
    except Exception:
        words = []

    # Если обычный текст пустой,
    # пытаемся собрать его из words
    if not text and words:

        sorted_words = sorted(
            words,
            key=lambda w: (
                round(float(w.get("top", 0)), 1),
                float(w.get("x0", 0))
            )
        )

        text = "\n".join(
            normalize_text(
                w.get("text", "")
            )
            for w in sorted_words
            if normalize_text(
                w.get("text", "")
            )
        )

    return normalize_text(text), words


def extract_pdf_pages(pdf_file):
    """
    Возвращает список:

    {
        page_index,
        text,
        words
    }
    """

    pdf_file.seek(0)

    result = []

    with pdfplumber.open(pdf_file) as pdf:

        for page_index, page in enumerate(pdf.pages, start=1):

            text, words = extract_page_content(page)

            result.append({
                "page_index": page_index,
                "text": text,
                "words": words,
            })

    return result


# ============================================================
# ПРЯМОЙ ПОИСК ПОЛНОГО НОМЕРА
# ============================================================

def find_direct_order_candidates(text, api_index):
    """
    Ищет уже готовый номер:

    0115480687-0268-1
    """

    if not text:
        return []

    candidates = []

    # Основной вариант
    patterns = [
        r"\b\d{5,14}-\d{2,8}-\d{1,4}\b",
        r"\b\d{4,14}\s*-\s*\d{2,8}\s*-\s*\d{1,4}\b",
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text
        ):

            raw = match.group(0)

            order = normalize_order(raw)

            if order in candidates:
                continue

            if normalize_key(order) in api_index:

                candidates.append(order)

    return candidates


# ============================================================
# НОВЫЙ ФОРМАТ II...
# ============================================================

def find_internal_codes(text):
    """
    Ищет внутренний номер нового формата.

    Например:

    II5010320
    II50103202549
    """

    if not text:
        return []

    result = []

    patterns = [
        r"\bII[A-Z0-9]{4,30}\b",
        r"\bIl[A-Z0-9]{4,30}\b",
        r"\bII\d{4,30}\b",
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text,
            flags=re.IGNORECASE
        ):

            value = match.group(0)

            value = value.upper()

            if value not in result:
                result.append(value)

    return result


# ============================================================
# СТАРЫЙ ФОРМАТ
# ============================================================

def get_text_lines(text):
    """
    Чистые строки PDF.
    """

    if not text:
        return []

    lines = []

    for line in text.splitlines():

        line = normalize_text(line)

        if line:
            lines.append(line)

    return lines


def extract_numeric_tokens(text):
    """
    Извлекает числовые блоки.

    Например из:

    -0268-1
    011548
    0687
    АНГАРСК_66

    получим:

    0268
    011548
    0687
    """

    if not text:
        return []

    result = []

    for line in get_text_lines(text):

        # не берем цифры, которые являются частью хвоста
        if looks_like_suffix(line):
            continue

        for token in re.findall(
            r"(?<![A-Za-zА-Яа-я0-9])\d{2,10}(?![A-Za-zА-Яа-я0-9])",
            line
        ):

            if token not in result:
                result.append(token)

    return result


def find_suffixes(text):
    """
    Находит хвосты:

    -0268-1
    -0110-2
    """

    if not text:
        return []

    result = []

    for match in re.finditer(
        r"-\d{2,8}-\d{1,4}",
        text
    ):

        suffix = match.group(0)

        if suffix not in result:
            result.append(suffix)

    return result


def build_old_order_candidates(text, api_index):
    """
    КЛЮЧЕВАЯ ФУНКЦИЯ.

    Старый формат:

        011548 0687 -0268-1

    или:

        6091 0687 -0110-2

    PDF может вернуть:

        -0268-1
        011548
        0687
        АНГАРСК_66

    Поэтому НЕ используем порядок текста.

    Берем:
        suffix
        numeric tokens

    и перебираем возможные комбинации.

    После сборки проверяем каждую комбинацию
    непосредственно по API.
    """

    if not text:
        return []

    suffixes = find_suffixes(text)

    if not suffixes:
        return []

    numeric_tokens = extract_numeric_tokens(text)

    if not numeric_tokens:
        return []

    candidates = []

    # --------------------------------------------------------
    # 1. Перебираем все пары числовых блоков
    # --------------------------------------------------------

    for suffix in suffixes:

        for first in numeric_tokens:

            # Первая часть обычно минимум 4 цифры
            if len(first) < 4:
                continue

            for second in numeric_tokens:

                if second == first:
                    continue

                # Номер старой этикетки — обычно 4 цифры.
                # Это важный фильтр, чтобы 0687 не смешивался
                # с адресами / номерами ПВЗ / другими цифрами.
                if len(second) != 4:
                    continue

                order = (
                    first
                    + second
                    + suffix
                )

                order = normalize_order(order)

                if not order:
                    continue

                # Самая важная проверка:
                # существует ли такой номер в API?
                if normalize_key(order) in api_index:

                    if order not in candidates:
                        candidates.append(order)

    return candidates


def find_old_format_candidates(text, api_index):
    """
    Дополнительные варианты старого формата.

    Обрабатываем случаи, когда PDF склеил:

        0115480687-0268-1

    или:

        011548 -0268-1

    или:

        011548
        0687
        -0268-1
    """

    result = []

    # --------------------------------------------------------
    # Вариант 1 — основная надежная логика
    # --------------------------------------------------------

    for order in build_old_order_candidates(
        text,
        api_index
    ):

        if order not in result:
            result.append(order)

    # --------------------------------------------------------
    # Вариант 2 — если номер уже есть целиком
    # --------------------------------------------------------

    patterns = [
        r"\b\d{8,14}-\d{2,8}-\d{1,4}\b",
        r"\b\d{4,14}\s+\d{4}\s+-\d{2,8}-\d{1,4}\b",
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text
        ):

            raw = match.group(0)

            order = normalize_order(raw)

            if normalize_key(order) in api_index:

                if order not in result:
                    result.append(order)

    # --------------------------------------------------------
    # Вариант 3 — анализ строк
    # --------------------------------------------------------

    lines = get_text_lines(text)

    suffix_positions = []

    for i, line in enumerate(lines):

        if looks_like_suffix(line):

            suffix_positions.append(
                (i, normalize_suffix(line))
            )

    for suffix_pos, suffix in suffix_positions:

        # Ищем числовые блоки в пределах страницы.
        nearby = []

        for i, line in enumerate(lines):

            if i == suffix_pos:
                continue

            if is_numeric_token(line):

                nearby.append(line)

        # Все комбинации
        for first in nearby:

            if len(first) < 4:
                continue

            for second in nearby:

                if second == first:
                    continue

                if len(second) != 4:
                    continue

                order = (
                    clean_number(first)
                    + clean_number(second)
                    + suffix
                )

                if normalize_key(order) in api_index:

                    if order not in result:
                        result.append(order)

    return result


# ============================================================
# ЛИСТ ПОДБОРА
# ============================================================

def build_picklist_index(picklist_pages):
    """
    Создает дополнительный индекс листа подбора.

    Используется ТОЛЬКО как fallback.

    Основным источником является API XLSX.
    """

    result = {}

    if not picklist_pages:
        return result

    for page in picklist_pages:

        text = page.get("text", "")

        if not text:
            continue

        # Ищем полные номера отправлений
        matches = re.findall(
            r"\b\d{4,14}-\d{2,8}-\d{1,4}\b",
            text
        )

        for match in matches:

            order = normalize_order(match)

            if order:
                result[normalize_key(order)] = order

    return result


def picklist_fallback(
    text,
    api_index,
    picklist_index
):
    """
    Если прямое распознавание не сработало,
    пробуем номера из листа подбора.
    """

    if not text:
        return []

    candidates = []

    # --------------------------------------------------------
    # Ищем хвост -XXXX-X
    # --------------------------------------------------------

    suffixes = find_suffixes(text)

    for suffix in suffixes:

        suffix_digits = suffix

        # Проверяем все известные API отправления
        for key, data in api_index.items():

            order = data.get("order", "")

            if order.endswith(suffix_digits):

                if order not in candidates:
                    candidates.append(order)

    # --------------------------------------------------------
    # Также ищем полный номер в тексте
    # --------------------------------------------------------

    for match in re.findall(
        r"\b\d{4,14}-\d{2,8}-\d{1,4}\b",
        text
    ):

        order = normalize_order(match)

        if normalize_key(order) in api_index:

            if order not in candidates:
                candidates.append(order)

    return candidates


# ============================================================
# РАЗРЕШЕНИЕ СТРАНИЦЫ
# ============================================================

def resolve_page(
    page,
    api_index,
    picklist_index=None
):
    """
    Возвращает информацию о странице:

    {
        type,
        key,
        order,
        api_found,
        data
    }
    """

    text = page.get("text", "")

    # --------------------------------------------------------
    # 1. Прямой полный номер
    # --------------------------------------------------------

    direct = find_direct_order_candidates(
        text,
        api_index
    )

    if direct:

        order = direct[0]

        data = get_order_data(
            api_index,
            order
        )

        return {
            "type": "Прямой номер",
            "key": order,
            "order": order,
            "api_found": True,
            "data": data,
        }

    # --------------------------------------------------------
    # 2. СТАРЫЙ ФОРМАТ
    # --------------------------------------------------------

    old_candidates = find_old_format_candidates(
        text,
        api_index
    )

    if old_candidates:

        order = old_candidates[0]

        data = get_order_data(
            api_index,
            order
        )

        return {
            "type": "Старый формат",
            "key": order,
            "order": order,
            "api_found": True,
            "data": data,
        }

    # --------------------------------------------------------
    # 3. Новый формат II...
    # --------------------------------------------------------

    internal_codes = find_internal_codes(text)

    if internal_codes:

        internal_code = internal_codes[0]

        # Новый II-код сам по себе не является номером
        # отправления.
        #
        # Поэтому НЕ подменяем им order.
        #
        # Пока оставляем его как диагностический ключ.

        return {
            "type": "Новый формат",
            "key": internal_code,
            "order": "",
            "api_found": False,
            "data": None,
        }

    # --------------------------------------------------------
    # 4. Picklist fallback
    # --------------------------------------------------------

    fallback = picklist_fallback(
        text,
        api_index,
        picklist_index or {}
    )

    if fallback:

        order = fallback[0]

        data = get_order_data(
            api_index,
            order
        )

        return {
            "type": "Лист подбора",
            "key": order,
            "order": order,
            "api_found": True,
            "data": data,
        }

    # --------------------------------------------------------
    # 5. Не распознано
    # --------------------------------------------------------

    return {
        "type": "Не распознано",
        "key": "",
        "order": "",
        "api_found": False,
        "data": None,
    }


# ============================================================
# ПЕРЕНОС ТЕКСТА НА ИНФО-НАКЛЕЙКУ
# ============================================================

def wrap_text(
    text,
    font_name,
    font_size,
    max_width
):
    """
    Перенос текста по ширине.
    """

    text = normalize_text(text)

    if not text:
        return [""]

    words = text.split()

    lines = []
    current = ""

    for word in words:

        candidate = (
            word
            if not current
            else current + " " + word
        )

        try:
            width = pdfmetrics.stringWidth(
                candidate,
                font_name,
                font_size
            )
        except Exception:
            width = len(candidate) * font_size * 0.5

        if width <= max_width:

            current = candidate

        else:

            if current:
                lines.append(current)

            # Если само слово длиннее ширины,
            # режем его посимвольно.
            if pdfmetrics.stringWidth(
                word,
                font_name,
                font_size
            ) > max_width:

                part = ""

                for char in word:

                    test = part + char

                    if pdfmetrics.stringWidth(
                        test,
                        font_name,
                        font_size
                    ) <= max_width:

                        part = test

                    else:

                        if part:
                            lines.append(part)

                        part = char

                current = part

            else:

                current = word

    if current:
        lines.append(current)

    return lines


# ============================================================
# ИНФОРМАЦИОННАЯ СТРАНИЦА 58×40
# ============================================================

def create_info_page(
    order,
    article,
    name,
    quantity
):
    """
    Создает отдельный PDF размером 58×40 мм.
    """

    buffer = BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=(
            LABEL_WIDTH,
            LABEL_HEIGHT
        )
    )

    left = 3 * mm
    right = 3 * mm

    usable_width = (
        LABEL_WIDTH
        - left
        - right
    )

    y = LABEL_HEIGHT - 4.5 * mm

    # --------------------------------------------------------
    # Заказ
    # --------------------------------------------------------

    c.setFont(
        FONT_BOLD,
        INFO_TITLE_SIZE
    )

    c.drawString(
        left,
        y,
        "Заказ:"
    )

    order_text = (
        order
        if order
        else "НЕ РАСПОЗНАН"
    )

    c.setFont(
        FONT_REGULAR,
        INFO_FONT_SIZE
    )

    order_lines = wrap_text(
        order_text,
        FONT_REGULAR,
        INFO_FONT_SIZE,
        usable_width - 15 * mm
    )

    if order_lines:

        c.drawString(
            left + 12 * mm,
            y,
            order_lines[0]
        )

    y -= 5.5 * mm

    # --------------------------------------------------------
    # Артикул
    # --------------------------------------------------------

    c.setFont(
        FONT_BOLD,
        INFO_FONT_SIZE
    )

    c.drawString(
        left,
        y,
        "Арт:"
    )

    c.setFont(
        FONT_REGULAR,
        INFO_FONT_SIZE
    )

    article_text = (
        article
        if article
        else "-"
    )

    article_lines = wrap_text(
        article_text,
        FONT_REGULAR,
        INFO_FONT_SIZE,
        usable_width - 12 * mm
    )

    if article_lines:

        c.drawString(
            left + 10 * mm,
            y,
            article_lines[0]
        )

    y -= 5.5 * mm

    # --------------------------------------------------------
    # Количество
    # --------------------------------------------------------

    c.setFont(
        FONT_BOLD,
        INFO_FONT_SIZE
    )

    c.drawString(
        left,
        y,
        "КОЛ-ВО:"
    )

    c.setFont(
        FONT_REGULAR,
        INFO_FONT_SIZE
    )

    quantity_text = (
        quantity
        if quantity
        else "?"
    )

    c.drawString(
        left + 14 * mm,
        y,
        str(quantity_text)
    )

    y -= 5.5 * mm

    # --------------------------------------------------------
    # Название
    # --------------------------------------------------------

    c.setFont(
        FONT_BOLD,
        INFO_FONT_SIZE
    )

    c.drawString(
        left,
        y,
        "Название:"
    )

    y -= 3.8 * mm

    c.setFont(
        FONT_REGULAR,
        INFO_FONT_SIZE
    )

    name_text = (
        name
        if name
        else "НЕ НАЙДЕНО"
    )

    name_lines = wrap_text(
        name_text,
        FONT_REGULAR,
        INFO_FONT_SIZE,
        usable_width
    )

    for line in name_lines[:MAX_TITLE_LINES]:

        c.drawString(
            left,
            y,
            line
        )

        y -= 3.7 * mm

    c.save()

    buffer.seek(0)

    return buffer


# ============================================================
# СОЗДАНИЕ РЕЗУЛЬТИРУЮЩЕГО PDF
# ============================================================

def build_result_pdf(
    original_pdf_file,
    page_results
):
    """
    На каждую исходную страницу:

    1. оригинальная этикетка
    2. информационная этикетка 58×40 мм
    """

    original_pdf_file.seek(0)

    reader = PdfReader(
        original_pdf_file
    )

    writer = PdfWriter()

    for index, result in enumerate(
        page_results
    ):

        # ----------------------------------------------------
        # Оригинальная страница
        # ----------------------------------------------------

        if index < len(reader.pages):

            writer.add_page(
                reader.pages[index]
            )

        # ----------------------------------------------------
        # Информационная страница
        # ----------------------------------------------------

        data = result.get(
            "data"
        )

        if data:

            order = data.get(
                "order",
                result.get("order", "")
            )

            article = data.get(
                "article",
                ""
            )

            name = data.get(
                "name",
                ""
            )

            quantity = data.get(
                "quantity",
                ""
            )

        else:

            order = result.get(
                "order",
                ""
            )

            article = ""
            name = "НЕ НАЙДЕНО"
            quantity = "?"

        info_pdf = create_info_page(
            order=order,
            article=article,
            name=name,
            quantity=quantity
        )

        info_reader = PdfReader(
            info_pdf
        )

        writer.add_page(
            info_reader.pages[0]
        )

    output = BytesIO()

    writer.write(output)

    output.seek(0)

    return output


# ============================================================
# ДИАГНОСТИКА
# ============================================================

def diagnostics_dataframe(
    page_results,
    pages
):
    rows = []

    for i, result in enumerate(
        page_results
    ):

        page_number = i + 1

        page = (
            pages[i]
            if i < len(pages)
            else {}
        )

        text = page.get(
            "text",
            ""
        )

        data = result.get(
            "data"
        )

        rows.append({
            "Страница": page_number,
            "Тип": result.get(
                "type",
                ""
            ),
            "Ключ": result.get(
                "key",
                ""
            ),
            "Заказ": result.get(
                "order",
                ""
            ),
            "API": (
                "ДА"
                if result.get(
                    "api_found",
                    False
                )
                else "НЕТ"
            ),
            "Арт": (
                data.get("article", "")
                if data
                else ""
            ),
            "Название": (
                data.get("name", "")
                if data
                else ""
            ),
            "КОЛ-ВО": (
                data.get("quantity", "")
                if data
                else "?"
            ),
            "Текст": text[:500],
        })

    return pd.DataFrame(rows)


# ============================================================
# ПОЛУЧЕНИЕ ИЗВЕСТНЫХ ОТПРАВЛЕНИЙ
# ============================================================

def get_known_postings(api_index):
    return [
        data.get("order", "")
        for data in api_index.values()
        if data.get("order")
    ]


# ============================================================
# STREAMLIT UI
# ============================================================

st.title("📦 Ozon FBS — обработка этикеток")

st.markdown(
    """
Загрузите:

1. **PDF с этикетками Ozon**
2. **API XLSX/CSV**
3. При необходимости — **Лист подбора**

На каждую исходную этикетку будет добавлена информационная
этикетка размером **58×40 мм**.
"""
)

st.divider()


# ============================================================
# ФАЙЛЫ
# ============================================================

col1, col2 = st.columns(2)

with col1:

    labels_file = st.file_uploader(
        "📄 PDF с этикетками",
        type=["pdf"],
        key="labels_pdf"
    )

with col2:

    api_file = st.file_uploader(
        "📊 API XLSX / CSV",
        type=[
            "xlsx",
            "xls",
            "csv"
        ],
        key="api_file"
    )

picklist_file = st.file_uploader(
    "📋 Лист подбора PDF — необязательно",
    type=["pdf"],
    key="picklist_pdf"
)


# ============================================================
# ОБРАБОТКА
# ============================================================

if labels_file and api_file:

    if st.button(
        "🚀 Обработать",
        type="primary",
        use_container_width=True
    ):

        with st.spinner(
            "Читаю API-файл..."
        ):

            df_api = read_api_file(
                api_file
            )

        if df_api is None:
            st.stop()

        api_index = build_api_index(
            df_api
        )

        if not api_index:

            st.error(
                "Не удалось построить индекс отправлений из API-файла."
            )

            st.stop()

        st.success(
            f"В API найдено отправлений: "
            f"{len(api_index):,}".replace(
                ",",
                " "
            )
        )

        # ----------------------------------------------------
        # Читаем основной PDF
        # ----------------------------------------------------

        with st.spinner(
            "Извлекаю текст из PDF этикеток..."
        ):

            labels_pages = extract_pdf_pages(
                labels_file
            )

        st.info(
            f"Страниц в исходном PDF: "
            f"{len(labels_pages)}"
        )

        # ----------------------------------------------------
        # Лист подбора
        # ----------------------------------------------------

        picklist_pages = []

        if picklist_file:

            with st.spinner(
                "Читаю лист подбора..."
            ):

                picklist_pages = extract_pdf_pages(
                    picklist_file
                )

        picklist_index = build_picklist_index(
            picklist_pages
        )

        # ----------------------------------------------------
        # Распознавание
        # ----------------------------------------------------

        page_results = []

        progress = st.progress(0)

        for i, page in enumerate(
            labels_pages
        ):

            result = resolve_page(
                page,
                api_index,
                picklist_index
            )

            page_results.append(
                result
            )

            progress.progress(
                (i + 1)
                / len(labels_pages)
            )

        progress.empty()

        # ----------------------------------------------------
        # Статистика
        # ----------------------------------------------------

        recognized = sum(
            1
            for r in page_results
            if r.get("api_found")
        )

        unresolved = (
            len(page_results)
            - recognized
        )

        old_format = sum(
            1
            for r in page_results
            if r.get("type")
            == "Старый формат"
        )

        new_format = sum(
            1
            for r in page_results
            if r.get("type")
            == "Новый формат"
        )

        direct = sum(
            1
            for r in page_results
            if r.get("type")
            == "Прямой номер"
        )

        st.subheader(
            "📊 Результат распознавания"
        )

        c1, c2, c3, c4, c5 = st.columns(5)

        c1.metric(
            "Всего",
            len(page_results)
        )

        c2.metric(
            "Распознано",
            recognized
        )

        c3.metric(
            "Не распознано",
            unresolved
        )

        c4.metric(
            "Старый формат",
            old_format
        )

        c5.metric(
            "Новый формат",
            new_format
        )

        # ----------------------------------------------------
        # Диагностика
        # ----------------------------------------------------

        st.subheader(
            "🔎 Диагностика"
        )

        diag_df = diagnostics_dataframe(
            page_results,
            labels_pages
        )

        st.dataframe(
            diag_df,
            use_container_width=True,
            hide_index=True
        )

        # ----------------------------------------------------
        # Нераспознанные
        # ----------------------------------------------------

        unresolved_indices = [
            i
            for i, result
            in enumerate(page_results)
            if not result.get("api_found")
        ]

        if unresolved_indices:

            st.warning(
                f"Не распознано страниц: "
                f"{len(unresolved_indices)}"
            )

            st.subheader(
                "🧩 Текст нераспознанных этикеток"
            )

            for i in unresolved_indices:

                page_number = i + 1

                result = page_results[i]

                text = labels_pages[i].get(
                    "text",
                    ""
                )

                with st.expander(
                    f"Страница {page_number} — "
                    f"{result.get('type', 'Не распознано')}"
                ):

                    st.write(
                        f"**Тип:** "
                        f"{result.get('type', '-')}"
                    )

                    st.write(
                        f"**Ключ:** "
                        f"{result.get('key', '-') or '-'}"
                    )

                    st.write(
                        f"**Заказ:** "
                        f"{result.get('order', '-') or '-'}"
                    )

                    st.code(
                        text
                        if text
                        else "[Текст PDF не извлечен]"
                    )

        # ----------------------------------------------------
        # Создание PDF
        # ----------------------------------------------------

        with st.spinner(
            "Формирую итоговый PDF..."
        ):

            # labels_file может быть уже прочитан,
            # поэтому возвращаем указатель в начало
            labels_file.seek(0)

            result_pdf = build_result_pdf(
                labels_file,
                page_results
            )

        st.success(
            "PDF готов."
        )

        st.download_button(
            label="⬇️ Скачать готовый PDF",
            data=result_pdf.getvalue(),
            file_name="ozon_fbs_labels_result.pdf",
            mime="application/pdf",
            use_container_width=True
        )

        # ----------------------------------------------------
        # Дополнительная диагностика старого формата
        # ----------------------------------------------------

        st.divider()

        st.subheader(
            "🧪 Проверка старого формата"
        )

        st.markdown(
            """
Для старых этикеток используется схема:

`первая часть + 4 цифры + -XXXX-X`

Например:

`011548 + 0687 + -0268-1`

→ `0115480687-0268-1`

Номер считается найденным только если такой номер
существует в API-файле.
"""
        )

        old_rows = []

        for i, result in enumerate(
            page_results
        ):

            if result.get("type") != "Старый формат":
                continue

            text = labels_pages[i].get(
                "text",
                ""
            )

            old_rows.append({
                "Страница": i + 1,
                "Извлеченный текст": text,
                "Результат": result.get(
                    "order",
                    ""
                ),
                "API": (
                    "ДА"
                    if result.get(
                        "api_found"
                    )
                    else "НЕТ"
                ),
            })

        if old_rows:

            st.dataframe(
                pd.DataFrame(old_rows),
                use_container_width=True,
                hide_index=True
            )
        else:

            st.info(
                "Старых этикеток в распознанных страницах не найдено."
            )


# ============================================================
# ЕСЛИ ФАЙЛЫ НЕ ЗАГРУЖЕНЫ
# ============================================================

else:

    st.info(
        "Загрузите PDF этикеток и API XLSX/CSV."
    )

    with st.expander(
        "ℹ️ Как распознается старая этикетка"
    ):

        st.markdown(
            """
### Пример 1

На этикетке визуально:

```text
011548 0687 -0268-1
АНГАРСК_66
