import streamlit as st
import re
import os
from io import BytesIO

from pypdf import PdfReader, PdfWriter
import pdfplumber

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import mm


# ============================================================
# НАСТРОЙКИ
# ============================================================

INFO_LABEL_WIDTH = 58 * mm
INFO_LABEL_HEIGHT = 40 * mm

FONT_NAME = "Roboto"

# Размер количества
QUANTITY_FONT_SIZE = 12

# ============================================================
# ШРИФТ
# ============================================================

def register_fonts():
    """
    Загружаем Roboto.
    DejaVu намеренно не используется.
    """

    candidates = [
        "Roboto_Full_Final.ttf",
        "Roboto-Regular.ttf",
        "OzonFont_Fix.ttf",
    ]

    for font_file in candidates:
        if os.path.exists(font_file):
            try:
                pdfmetrics.registerFont(
                    TTFont(FONT_NAME, font_file)
                )
                return font_file
            except Exception:
                pass

    return None


FONT_FILE = register_fonts()


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================

def normalize_text(value):
    if value is None:
        return ""

    value = str(value)

    value = value.replace("\xa0", " ")
    value = value.replace("\u200b", "")
    value = value.replace("\ufeff", "")

    value = re.sub(r"[ \t]+", " ", value)

    return value.strip()


def normalize_order(value):
    """
    Нормализация номера отправления.

    Пример:
    0208327146-0066-1
    """

    if value is None:
        return ""

    value = str(value).strip()

    value = value.replace("\xa0", "")
    value = value.replace(" ", "")
    value = value.replace("–", "-")
    value = value.replace("—", "-")

    return value


def normalize_key(value):
    if value is None:
        return ""

    value = normalize_text(value)

    return value.upper()


def clean_number(value):
    if value is None:
        return ""

    value = str(value).strip()

    value = value.replace("\xa0", "")
    value = value.replace(" ", "")
    value = value.replace(",", ".")

    return value


# ============================================================
# ОЧИСТКА КОЛИЧЕСТВА
# ============================================================

def clean_quantity(value):
    """
    Возвращает только корректное количество товара.

    Главная задача:
    не допустить попадания посторонних цифр
    в поле КОЛ-ВО.

    Допустимые варианты:
        1
        2
        10
        1.0
        2,0
        1 шт
        2 шт.

    Если значение явно не является количеством,
    возвращаем исходное очищенное значение.
    """

    if value is None:
        return ""

    value = normalize_text(value)

    if not value:
        return ""

    # --------------------------------------------------------
    # Если это обычное целое число
    # --------------------------------------------------------

    match = re.fullmatch(
        r"(\d+)",
        value
    )

    if match:
        return match.group(1)

    # --------------------------------------------------------
    # Десятичное значение:
    # 1.0 / 1,0
    # --------------------------------------------------------

    match = re.fullmatch(
        r"(\d+)[.,](\d+)",
        value
    )

    if match:

        integer_part = match.group(1)
        decimal_part = match.group(2)

        # Если 1.0 / 2.00 — показываем как целое
        if set(decimal_part) <= {"0"}:
            return integer_part

        return (
            f"{integer_part}."
            f"{decimal_part}"
        )

    # --------------------------------------------------------
    # Форматы "1 шт", "2 штуки"
    # --------------------------------------------------------

    match = re.fullmatch(
        r"(\d+)\s*(?:шт\.?|штук[аи]?|ед\.?)?",
        value,
        flags=re.IGNORECASE
    )

    if match:
        return match.group(1)

    # --------------------------------------------------------
    # Иногда Excel отдаёт 1.0 как float
    # --------------------------------------------------------

    try:

        numeric = float(
            value.replace(",", ".")
        )

        if numeric.is_integer():

            return str(
                int(numeric)
            )

    except Exception:
        pass

    return value


def is_numeric_token(value):
    if not value:
        return False

    value = str(value).strip()

    return bool(
        re.fullmatch(r"\d+", value)
    )


def looks_like_suffix(value):
    if not value:
        return False

    return bool(
        re.fullmatch(
            r"-\d{2,8}-\d{1,4}",
            str(value).strip()
        )
    )


def normalize_suffix(value):
    if not value:
        return ""

    value = str(value).strip()

    value = value.replace("–", "-")
    value = value.replace("—", "-")

    if not value.startswith("-"):
        value = "-" + value

    return value


# ============================================================
# ПОИСК КОЛОНКИ
# ============================================================

def find_column(df, variants):
    """
    Ищет колонку по нескольким возможным названиям.
    """

    normalized_columns = {}

    for col in df.columns:
        key = normalize_text(col).lower()
        normalized_columns[key] = col

    for variant in variants:
        variant_key = normalize_text(variant).lower()

        if variant_key in normalized_columns:
            return normalized_columns[variant_key]

    # Более мягкий поиск
    for col in df.columns:
        col_norm = normalize_text(col).lower()

        for variant in variants:
            variant_norm = normalize_text(variant).lower()

            if variant_norm in col_norm:
                return col

    return None


# ============================================================
# ЧТЕНИЕ API ФАЙЛА
# ============================================================

def read_api_file(uploaded_file):
    """
    Читает XLSX / XLS / CSV.
    """

    import pandas as pd

    if uploaded_file is None:
        return None

    filename = uploaded_file.name.lower()

    try:

        if filename.endswith(".csv"):

            raw = uploaded_file.getvalue()

            encodings = [
                "utf-8-sig",
                "utf-8",
                "cp1251",
            ]

            for encoding in encodings:
                try:
                    text = raw.decode(encoding)

                    from io import StringIO

                    return pd.read_csv(
                        StringIO(text),
                        sep=None,
                        engine="python"
                    )

                except Exception:
                    continue

            raise ValueError(
                "Не удалось прочитать CSV"
            )

        if filename.endswith(
            (".xlsx", ".xls")
        ):

            return pd.read_excel(
                uploaded_file
            )

        raise ValueError(
            "Поддерживаются XLSX, XLS и CSV"
        )

    except Exception as e:
        raise ValueError(
            f"Ошибка чтения API-файла: {e}"
        )


# ============================================================
# ИНДЕКС API
# ============================================================

def build_api_index(df):

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

    quantity_col = find_column(
        df,
        [
            "количество",
            "кол-во",
            "колво",
            "qty",
            "quantity",
        ]
    )

    if not order_col:
        raise ValueError(
            "В API-файле не найдена колонка номера отправления."
        )

    index = {}

    for _, row in df.iterrows():

        order = normalize_order(
            row.get(order_col, "")
        )

        if not order:
            continue

        article = ""

        if article_col:
            article = normalize_text(
                row.get(article_col, "")
            )

        name = ""

        if name_col:
            name = normalize_text(
                row.get(name_col, "")
            )

        quantity = ""

        if quantity_col:
            quantity = clean_quantity(
                row.get(quantity_col, "")
            )

        index[order] = {
            "order": order,
            "article": article,
            "name": name,
            "quantity": quantity,
            "row": row.to_dict(),
        }

    return index


# ============================================================
# ПОЛУЧЕНИЕ ДАННЫХ ЗАКАЗА
# ============================================================

def get_order_data(order, api_index):

    order = normalize_order(order)

    if not order:
        return None

    return api_index.get(order)


# ============================================================
# ИЗВЛЕЧЕНИЕ СТРАНИЦЫ
# ============================================================

def extract_page_content(page):
    """
    Извлекает текст и отдельные линии PDF.
    """

    text = ""

    try:
        text = page.extract_text() or ""
    except Exception:
        text = ""

    lines = []

    for line in text.splitlines():

        line = normalize_text(line)

        if line:
            lines.append(line)

    return {
        "text": "\n".join(lines),
        "lines": lines,
    }


# ============================================================
# ИЗВЛЕЧЕНИЕ PDF
# ============================================================

def extract_pdf_pages(uploaded_pdf):

    uploaded_pdf.seek(0)

    pages = []

    with pdfplumber.open(uploaded_pdf) as pdf:

        for page_number, page in enumerate(
            pdf.pages,
            start=1
        ):

            content = extract_page_content(page)

            pages.append({
                "page": page_number,
                "text": content["text"],
                "lines": content["lines"],
            })

    return pages


# ============================================================
# ПРЯМОЙ ПОИСК ПОЛНОГО НОМЕРА
# ============================================================

def find_direct_order_candidates(
    text,
    api_index
):

    candidates = []

    if not text:
        return candidates

    order_pattern = re.compile(
        r"\b\d{4,14}-\d{2,8}-\d{1,4}\b"
    )

    for match in order_pattern.finditer(text):

        order = normalize_order(
            match.group(0)
        )

        if order in api_index:
            candidates.append(order)

    return list(dict.fromkeys(candidates))


# ============================================================
# II КОДЫ
# ============================================================

def find_internal_codes(text):

    if not text:
        return []

    result = []

    pattern = re.compile(
        r"\bII\d+\b",
        re.IGNORECASE
    )

    for match in pattern.finditer(text):

        code = normalize_text(
            match.group(0)
        ).upper()

        if code not in result:
            result.append(code)

    return result


# ============================================================
# СТРОКИ
# ============================================================

def get_text_lines(text):

    if not text:
        return []

    result = []

    for line in text.splitlines():

        line = normalize_text(line)

        if line:
            result.append(line)

    return result


# ============================================================
# ЧИСЛОВЫЕ ТОКЕНЫ
# ============================================================

def extract_numeric_tokens(text):

    tokens = []

    lines = get_text_lines(text)

    for line in lines:

        # Не берём числа из полноценного номера заказа
        if re.search(
            r"\d{4,14}-\d{2,8}-\d{1,4}",
            line
        ):
            continue

        for token in re.findall(
            r"\b\d+\b",
            line
        ):

            if token:
                tokens.append(token)

    return tokens


# ============================================================
# SUFFIX
# ============================================================

def find_suffixes(text):

    if not text:
        return []

    result = []

    pattern = re.compile(
        r"-\d{2,8}-\d{1,4}"
    )

    for match in pattern.finditer(text):

        suffix = normalize_suffix(
            match.group(0)
        )

        if suffix not in result:
            result.append(suffix)

    return result


# ============================================================
# СТАРЫЙ ФОРМАТ
#
# Например:
#
# 011548
# 0687
# -0268-1
#
# =>
# 0115480687-0268-1
# ============================================================

def build_old_order_candidates(
    text,
    api_index
):

    candidates = []

    if not text:
        return candidates

    suffixes = find_suffixes(text)

    if not suffixes:
        return candidates

    numeric_tokens = extract_numeric_tokens(
        text
    )

    numeric_tokens = list(
        dict.fromkeys(numeric_tokens)
    )

    for suffix in suffixes:

        for i, first in enumerate(
            numeric_tokens
        ):

            if len(first) < 4:
                continue

            for j, second in enumerate(
                numeric_tokens
            ):

                if i == j:
                    continue

                if len(second) != 4:
                    continue

                candidate = normalize_order(
                    first + second + suffix
                )

                if candidate in api_index:

                    if candidate not in candidates:
                        candidates.append(
                            candidate
                        )

    return candidates


def find_old_format_candidates(
    text,
    api_index
):

    result = []

    direct = find_direct_order_candidates(
        text,
        api_index
    )

    for order in direct:

        if order not in result:
            result.append(order)

    old = build_old_order_candidates(
        text,
        api_index
    )

    for order in old:

        if order not in result:
            result.append(order)

    return result


# ============================================================
# ЛИСТ ПОДБОРА
#
# Для нового формата индексируем:
#
# II5010320 + 2714
#
# а НЕ просто 2714.
# ============================================================

def build_picklist_index(
    picklist_pages
):

    index = {
        "orders": {},
        "new_labels": {},
    }

    order_re = re.compile(
        r"\b\d{4,14}-\d{2,8}-\d{1,4}\b"
    )

    new_label_re = re.compile(
        r"\b(II\d+)\s*(\d{4})\b",
        re.IGNORECASE
    )

    for page_num, text in enumerate(
        picklist_pages,
        start=1
    ):

        if not text:
            continue

        text = normalize_text(text)

        orders = list(
            order_re.finditer(text)
        )

        new_labels = list(
            new_label_re.finditer(text)
        )

        # --------------------------------------------------------
        # Все номера заказов на странице
        # --------------------------------------------------------

        for match in orders:

            order = normalize_order(
                match.group(0)
            )

            if not order:
                continue

            index["orders"].setdefault(
                order,
                {
                    "page": page_num,
                    "text": text,
                }
            )

        # --------------------------------------------------------
        # Новые II-этикетки
        # --------------------------------------------------------

        for nm in new_labels:

            internal_code = normalize_text(
                nm.group(1)
            ).upper()

            short_key = str(
                nm.group(2)
            ).strip()

            pair_key = (
                f"{internal_code}|{short_key}"
            )

            # ----------------------------------------------------
            # Все номера заказов ДО II
            # ----------------------------------------------------

            previous_orders = [
                om for om in orders
                if om.start() < nm.start()
            ]

            if not previous_orders:
                continue

            nearest_order_match = (
                previous_orders[-1]
            )

            order = normalize_order(
                nearest_order_match.group(0)
            )

            if not order:
                continue

            # ----------------------------------------------------
            # Между номером и II не должно быть
            # другого номера заказа
            # ----------------------------------------------------

            distance_text = text[
                nearest_order_match.end():
                nm.start()
            ]

            if order_re.search(
                distance_text
            ):
                continue

            data = {
                "order": order,
                "page": page_num,
                "text": text,
                "internal_code": internal_code,
                "short_key": short_key,
            }

            # ----------------------------------------------------
            # Проверка конфликта
            # ----------------------------------------------------

            if pair_key in index["new_labels"]:

                old_data = index[
                    "new_labels"
                ][pair_key]

                old_order = old_data.get(
                    "order"
                )

                if (
                    not old_data.get("ambiguous")
                    and old_order != order
                ):

                    orders_list = []

                    if old_order:
                        orders_list.append(
                            old_order
                        )

                    orders_list.append(
                        order
                    )

                    index["new_labels"][
                        pair_key
                    ] = {
                        "ambiguous": True,
                        "orders": list(
                            dict.fromkeys(
                                orders_list
                            )
                        ),
                        "page": page_num,
                        "text": text,
                        "internal_code":
                            internal_code,
                        "short_key":
                            short_key,
                    }

                elif old_data.get(
                    "ambiguous"
                ):

                    old_orders = old_data.get(
                        "orders",
                        []
                    )

                    if order not in old_orders:

                        old_orders.append(
                            order
                        )

                        old_data[
                            "orders"
                        ] = old_orders

                continue

            index["new_labels"][
                pair_key
            ] = data

    return index


# ============================================================
# FALLBACK ПО ЛИСТУ ПОДБОРА
#
# Для нового формата fallback запрещён.
# ============================================================

def picklist_fallback(
    text,
    api_index,
    picklist_index
):

    if not text:
        return None

    # Полный номер прямо на этикетке
    direct = find_direct_order_candidates(
        text,
        api_index
    )

    if len(direct) == 1:

        order = direct[0]

        return {
            "order": order,
            "api": api_index.get(order),
            "source": "PDF",
        }

    # Старый формат
    old_candidates = find_old_format_candidates(
        text,
        api_index
    )

    if len(old_candidates) == 1:

        order = old_candidates[0]

        return {
            "order": order,
            "api": api_index.get(order),
            "source": "Старый формат",
        }

    return None


# ============================================================
# РАСПОЗНАВАНИЕ СТРАНИЦЫ
# ============================================================

def resolve_page(
    text,
    api_index,
    picklist_index=None
):

    if not text:

        return {
            "type": "Не распознано",
            "key": "",
            "order": "",
            "api": None,
            "source": "",
            "internal_code": "",
            "short_key": "",
        }

    text_normalized = normalize_text(
        text
    )

    # ========================================================
    # 1. ПРЯМОЙ ПОЛНЫЙ НОМЕР
    # ========================================================

    direct = find_direct_order_candidates(
        text_normalized,
        api_index
    )

    if len(direct) == 1:

        order = direct[0]

        return {
            "type": "Прямой номер",
            "key": order,
            "order": order,
            "api": api_index.get(order),
            "source": "PDF",
            "internal_code": "",
            "short_key": "",
        }

    # ========================================================
    # 2. СТАРЫЙ ФОРМАТ
    # ========================================================

    old_candidates = find_old_format_candidates(
        text_normalized,
        api_index
    )

    if len(old_candidates) == 1:

        order = old_candidates[0]

        return {
            "type": "Старый формат",
            "key": order,
            "order": order,
            "api": api_index.get(order),
            "source": "PDF",
            "internal_code": "",
            "short_key": "",
        }

    # ========================================================
    # 3. НОВЫЙ ФОРМАТ II
    #
    # Например:
    #
    # II5010320 2714
    #
    # или:
    #
    # II50103202714
    #
    # ВАЖНО:
    #
    # 2714 НЕ ИЩЕМ В НОМЕРЕ ЗАКАЗА.
    #
    # Только:
    #
    # II5010320|2714
    #        ↓
    # Лист подбора
    #        ↓
    # Номер отправления
    # ========================================================

    new_label_matches = re.findall(
        r"\b(II\d+)\s*(\d{4})\b",
        text_normalized,
        flags=re.IGNORECASE
    )

    if new_label_matches:

        internal_code, short_key = (
            new_label_matches[0]
        )

        internal_code = normalize_text(
            internal_code
        ).upper()

        short_key = str(
            short_key
        ).strip()

        display_key = (
            f"{internal_code} {short_key}"
        )

        pair_key = (
            f"{internal_code}|{short_key}"
        )

        pick_data = None

        if picklist_index:

            pick_data = picklist_index.get(
                "new_labels",
                {}
            ).get(pair_key)

        # ----------------------------------------------------
        # КОНФЛИКТ
        # ----------------------------------------------------

        if pick_data and pick_data.get(
            "ambiguous"
        ):

            return {
                "type": "Новый формат",
                "key": display_key,
                "order": "",
                "api": None,
                "source":
                    "Лист подбора: НЕОДНОЗНАЧНО",
                "internal_code":
                    internal_code,
                "short_key":
                    short_key,
                "conflict_orders":
                    pick_data.get(
                        "orders",
                        []
                    ),
            }

        # ----------------------------------------------------
        # НАЙДЕН ОДНОЗНАЧНО
        # ----------------------------------------------------

        if pick_data:

            order = normalize_order(
                pick_data.get(
                    "order",
                    ""
                )
            )

            if order:

                api_data = api_index.get(
                    order
                )

                if api_data:

                    return {
                        "type":
                            "Новый формат",
                        "key":
                            display_key,
                        "order":
                            order,
                        "api":
                            api_data,
                        "source":
                            "Лист подбора",
                        "internal_code":
                            internal_code,
                        "short_key":
                            short_key,
                    }

                return {
                    "type":
                        "Новый формат",
                    "key":
                        display_key,
                    "order":
                        order,
                    "api":
                        None,
                    "source":
                        "Лист подбора, нет в API",
                    "internal_code":
                        internal_code,
                    "short_key":
                        short_key,
                }

        # ----------------------------------------------------
        # НЕ УГАДЫВАЕМ
        # ----------------------------------------------------

        return {
            "type": "Новый формат",
            "key": display_key,
            "order": "",
            "api": None,
            "source": "",
            "internal_code":
                internal_code,
            "short_key":
                short_key,
        }

    # ========================================================
    # 4. ОСТАЛЬНЫЕ FALLBACK
    # ========================================================

    fallback = picklist_fallback(
        text_normalized,
        api_index,
        picklist_index
    )

    if fallback:

        return {
            "type": "Лист подбора",
            "key": fallback["order"],
            "order": fallback["order"],
            "api": fallback["api"],
            "source": fallback["source"],
            "internal_code": "",
            "short_key": "",
        }

    # ========================================================
    # 5. НЕ РАСПОЗНАНО
    # ========================================================

    return {
        "type": "Не распознано",
        "key": "",
        "order": "",
        "api": None,
        "source": "",
        "internal_code": "",
        "short_key": "",
    }


# ============================================================
# ПЕРЕНОС СТРОК
# ============================================================

def wrap_text(
    text,
    max_chars
):

    if not text:
        return []

    text = str(text)

    words = text.split()

    lines = []

    current = ""

    for word in words:

        if not current:

            current = word

        elif len(
            current + " " + word
        ) <= max_chars:

            current += " " + word

        else:

            lines.append(current)

            current = word

    if current:
        lines.append(current)

    return lines


# ============================================================
# СОЗДАНИЕ ИНФО-ЭТИКЕТКИ
# ============================================================

def create_info_page(
    order_data,
    order="",
    internal_code="",
    short_key=""
):

    buffer = BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=(
            INFO_LABEL_WIDTH,
            INFO_LABEL_HEIGHT
        )
    )

    left = 3 * mm

    y = INFO_LABEL_HEIGHT - 5 * mm

    # ========================================================
    # НЕ РАСПОЗНАНО
    # ========================================================

    if not order_data:

        c.setFont(
            FONT_NAME,
            16
        )

        c.drawString(
            left,
            y,
            "ЗАКАЗ: НЕ РАСПОЗНАН"
        )

        y -= 7 * mm

        c.setFont(
            FONT_NAME,
            9
        )

        if internal_code:

            c.drawString(
                left,
                y,
                f"II: {internal_code}"
            )

            y -= 5 * mm

            if short_key:

                c.drawString(
                    left,
                    y,
                    f"Ключ: {short_key}"
                )

                y -= 5 * mm

        c.drawString(
            left,
            y,
            f"Отправление: {order or '-'}"
        )

        y -= 7 * mm

        c.drawString(
            left,
            y,
            "Арт: -"
        )

        y -= 8 * mm

        c.setFont(
            FONT_NAME,
            QUANTITY_FONT_SIZE
        )

        c.drawString(
            left,
            y,
            "КОЛ-ВО: ?"
        )

        y -= 8 * mm

        c.setFont(
            FONT_NAME,
            8
        )

        c.drawString(
            left,
            y,
            "ТРЕБУЕТ ПРОВЕРКИ"
        )

        c.showPage()
        c.save()

        buffer.seek(0)

        return buffer.getvalue()

    # ========================================================
    # ДАННЫЕ
    # ========================================================

    article = normalize_text(
        order_data.get(
            "article",
            ""
        )
    )

    name = normalize_text(
        order_data.get(
            "name",
            ""
        )
    )

    quantity = clean_quantity(
        order_data.get(
            "quantity",
            ""
        )
    )

    order = normalize_order(
        order
    )

    # ========================================================
    # НОМЕР ОТПРАВЛЕНИЯ
    # ========================================================

    c.setFont(
        FONT_NAME,
        9
    )

    c.drawString(
        left,
        y,
        f"Отправление: {order or '-'}"
    )

    y -= 6 * mm

    # ========================================================
    # II / УНИКАЛЬНЫЙ ИНДЕНТИФИКАТОР
    #
    # Добавляем ТОЛЬКО если это тестовая II-этикетка.
    # ========================================================

    if internal_code:

        c.setFont(
            FONT_NAME,
            8
        )

        c.drawString(
            left,
            y,
            f"II: {internal_code}"
        )

        y -= 4.5 * mm

        if short_key:

            c.drawString(
                left,
                y,
                f"Ключ: {short_key}"
            )

            y -= 5 * mm

    # ========================================================
    # АРТИКУЛ
    # ========================================================

    c.setFont(
        FONT_NAME,
        9
    )

    c.drawString(
        left,
        y,
        f"Арт: {article or '-'}"
    )

    y -= 8 * mm

    # ========================================================
    # КОЛИЧЕСТВО
    #
    # 16 pt
    # ========================================================

    c.setFont(
        FONT_NAME,
        QUANTITY_FONT_SIZE
    )

    c.drawString(
        left,
        y,
        f"КОЛ-ВО: {quantity or '?'}"
    )

    y -= 9 * mm

    # ========================================================
    # НАЗВАНИЕ
    # ========================================================

    c.setFont(
        FONT_NAME,
        7.5
    )

    c.drawString(
        left,
        y,
        "Название:"
    )

    y -= 3.5 * mm

    name_lines = wrap_text(
        name or "НЕ НАЙДЕНО",
        35
    )

    for line in name_lines[:2]:

        c.drawString(
            left,
            y,
            line
        )

        y -= 3.5 * mm

    c.showPage()
    c.save()

    buffer.seek(0)

    return buffer.getvalue()


# ============================================================
# СОЗДАНИЕ РЕЗУЛЬТИРУЮЩЕГО PDF
#
# На каждый физический заказ:
#
# 1. Оригинальная этикетка
# 2. Инфо-этикетка 58x40
# ============================================================

def build_result_pdf(
    original_pdf,
    page_results
):

    original_pdf.seek(0)

    reader = PdfReader(
        original_pdf
    )

    writer = PdfWriter()

    for index, result in enumerate(
        page_results
    ):

        if index >= len(
            reader.pages
        ):
            break

        # ----------------------------------------------------
        # ОРИГИНАЛЬНАЯ СТРАНИЦА
        # ----------------------------------------------------

        writer.add_page(
            reader.pages[index]
        )

        # ----------------------------------------------------
        # ИНФО-ЭТИКЕТКА
        # ----------------------------------------------------

        api_data = result.get(
            "api"
        )

        order = result.get(
            "order",
            ""
        )

        internal_code = result.get(
            "internal_code",
            ""
        )

        short_key = result.get(
            "short_key",
            ""
        )

        info_pdf = create_info_page(
            api_data,
            order,
            internal_code,
            short_key
        )

        info_reader = PdfReader(
            BytesIO(info_pdf)
        )

        writer.add_page(
            info_reader.pages[0]
        )

    output = BytesIO()

    writer.write(output)

    output.seek(0)

    return output.getvalue()


# ============================================================
# ДИАГНОСТИКА
# ============================================================

def diagnostics_dataframe(
    page_results
):

    import pandas as pd

    rows = []

    for item in page_results:

        api_data = item.get(
            "api"
        ) or {}

        rows.append({
            "Страница":
                item.get(
                    "page"
                ),

            "Тип":
                item.get(
                    "type",
                    ""
                ),

            "Ключ":
                item.get(
                    "key",
                    ""
                ),

            "Заказ":
                item.get(
                    "order",
                    ""
                ),

            "II":
                item.get(
                    "internal_code",
                    ""
                ),

            "Ключ II":
                item.get(
                    "short_key",
                    ""
                ),

            "API":
                "ДА"
                if api_data
                else "НЕТ",

            "Артикул":
                api_data.get(
                    "article",
                    ""
                ),

            "Название":
                api_data.get(
                    "name",
                    ""
                ),

            "Количество":
                clean_quantity(
                    api_data.get(
                        "quantity",
                        ""
                    )
                ),

            "Источник":
                item.get(
                    "source",
                    ""
                ),

            "Текст":
                item.get(
                    "text",
                    ""
                ),
        })

    return pd.DataFrame(
        rows
    )


# ============================================================
# ИЗВЕСТНЫЕ ОТПРАВЛЕНИЯ
# ============================================================

def get_known_postings(
    api_index
):

    return set(
        api_index.keys()
    )


# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(
    page_title="Ozon FBS — этикетки",
    layout="wide"
)

st.title(
    "Ozon FBS — сопоставление этикеток"
)

st.caption(
    "Оригинальная этикетка + информационная этикетка 58×40 мм"
)


# ============================================================
# ЗАГРУЗКИ
# ============================================================

st.subheader(
    "1. Исходные файлы"
)

original_pdf = st.file_uploader(
    "Оригинальные этикетки Ozon PDF",
    type=["pdf"],
    key="original_pdf"
)

api_file = st.file_uploader(
    "API XLSX / CSV",
    type=[
        "xlsx",
        "xls",
        "csv"
    ],
    key="api_file"
)

picklist_pdf = st.file_uploader(
    "Лист подбора PDF — необязательно",
    type=["pdf"],
    key="picklist_pdf"
)


# ============================================================
# ЗАПУСК
# ============================================================

if st.button(
    "🚀 Обработать",
    type="primary",
    use_container_width=True
):

    if not original_pdf:

        st.error(
            "Загрузите исходный PDF этикеток."
        )

        st.stop()

    if not api_file:

        st.error(
            "Загрузите API XLSX / CSV."
        )

        st.stop()

    try:

        # ====================================================
        # API
        # ====================================================

        with st.spinner(
            "Читаю API-файл..."
        ):

            df_api = read_api_file(
                api_file
            )

            api_index = build_api_index(
                df_api
            )

        st.success(
            f"API: найдено {len(api_index):,} отправлений"
        )

        # ====================================================
        # ОРИГИНАЛЬНЫЙ PDF
        # ====================================================

        with st.spinner(
            "Распознаю оригинальные этикетки..."
        ):

            pdf_pages = extract_pdf_pages(
                original_pdf
            )

        st.info(
            f"Страниц оригинального PDF: "
            f"{len(pdf_pages)}"
        )

        # ====================================================
        # ЛИСТ ПОДБОРА
        # ====================================================

        picklist_index = None

        if picklist_pdf:

            with st.spinner(
                "Читаю Лист подбора..."
            ):

                picklist_pages_raw = (
                    extract_pdf_pages(
                        picklist_pdf
                    )
                )

                picklist_texts = [
                    p["text"]
                    for p in picklist_pages_raw
                ]

                picklist_index = (
                    build_picklist_index(
                        picklist_texts
                    )
                )

            st.success(
                "Лист подбора загружен"
            )

            st.write(
                "Новых пар "
                f"`II-код + ключ`: "
                f"{len(picklist_index['new_labels'])}"
            )

        # ====================================================
        # РАСПОЗНАВАНИЕ
        # ====================================================

        page_results = []

        progress = st.progress(
            0
        )

        total = len(
            pdf_pages
        )

        for i, page in enumerate(
            pdf_pages
        ):

            text = page.get(
                "text",
                ""
            )

            result = resolve_page(
                text,
                api_index,
                picklist_index
            )

            result["page"] = page.get(
                "page",
                i + 1
            )

            result["text"] = text

            page_results.append(
                result
            )

            progress.progress(
                (i + 1) / total
            )

        progress.empty()

        # ====================================================
        # СТАТИСТИКА
        # ====================================================

        recognized = sum(
            1
            for r in page_results
            if r.get("api")
        )

        unresolved = total - recognized

        old_count = sum(
            1
            for r in page_results
            if r.get("type")
            == "Старый формат"
        )

        new_count = sum(
            1
            for r in page_results
            if r.get("type")
            == "Новый формат"
        )

        col1, col2, col3, col4, col5 = (
            st.columns(5)
        )

        col1.metric(
            "Всего",
            total
        )

        col2.metric(
            "Распознано",
            recognized
        )

        col3.metric(
            "Не распознано",
            unresolved
        )

        col4.metric(
            "Старый формат",
            old_count
        )

        col5.metric(
            "Новый формат",
            new_count
        )

        # ====================================================
        # ДИАГНОСТИКА
        # ====================================================

        st.subheader(
            "Результат распознавания"
        )

        diag_df = diagnostics_dataframe(
            page_results
        )

        st.dataframe(
            diag_df,
            use_container_width=True,
            height=600
        )

        # ====================================================
        # НЕРАСПОЗНАННЫЕ
        # ====================================================

        unresolved_rows = [
            r
            for r in page_results
            if not r.get("api")
        ]

        if unresolved_rows:

            st.subheader(
                "⚠️ Не распознано"
            )

            for r in unresolved_rows:

                with st.expander(
                    f"Страница {r.get('page')} — "
                    f"{r.get('type')}"
                ):

                    st.write(
                        f"**Тип:** "
                        f"{r.get('type')}"
                    )

                    st.write(
                        f"**Ключ:** "
                        f"{r.get('key')}"
                    )

                    st.write(
                        f"**II:** "
                        f"{r.get('internal_code') or '-'}"
                    )

                    st.write(
                        f"**Ключ II:** "
                        f"{r.get('short_key') or '-'}"
                    )

                    st.write(
                        f"**Заказ:** "
                        f"{r.get('order') or '-'}"
                    )

                    st.write(
                        f"**Источник:** "
                        f"{r.get('source') or '-'}"
                    )

                    conflict_orders = r.get(
                        "conflict_orders",
                        []
                    )

                    if conflict_orders:

                        st.warning(
                            "Найдены разные номера "
                            "отправлений для одной пары II + ключ:"
                        )

                        for conflict_order in conflict_orders:

                            st.write(
                                f"• {conflict_order}"
                            )

                    st.code(
                        r.get(
                            "text",
                            ""
                        )
                    )

        # ====================================================
        # ПРОВЕРКА СТАРОГО ФОРМАТА
        # ====================================================

        old_rows = [
            r
            for r in page_results
            if r.get("type")
            == "Старый формат"
        ]

        if old_rows:

            st.subheader(
                "Проверка старого формата"
            )

            old_df = diagnostics_dataframe(
                old_rows
            )

            st.dataframe(
                old_df,
                use_container_width=True
            )

        # ====================================================
        # ПРОВЕРКА НОВОГО ФОРМАТА
        # ====================================================

        new_rows = [
            r
            for r in page_results
            if r.get("type")
            == "Новый формат"
        ]

        if new_rows:

            st.subheader(
                "Проверка нового формата"
            )

            new_df = diagnostics_dataframe(
                new_rows
            )

            st.dataframe(
                new_df,
                use_container_width=True
            )

        # ====================================================
        # СОЗДАНИЕ РЕЗУЛЬТАТА
        # ====================================================

        with st.spinner(
            "Формирую итоговый PDF..."
        ):

            result_pdf = build_result_pdf(
                original_pdf,
                page_results
            )

        st.success(
            "Готово. На каждый исходный лист "
            "добавлена информационная этикетка 58×40 мм."
        )

        st.download_button(
            "⬇️ Скачать готовый PDF",
            data=result_pdf,
            file_name="ozon_fbs_labels_result.pdf",
            mime="application/pdf",
            use_container_width=True
        )

    except Exception as e:

        st.error(
            f"Ошибка: {e}"
        )

        st.exception(e)
