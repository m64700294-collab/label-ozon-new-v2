import streamlit as st
import os
import re
import io
import csv
from collections import defaultdict, Counter

import pandas as pd
import pdfplumber

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4


# ============================================================
# НАСТРОЙКИ
# ============================================================

st.set_page_config(
    page_title="Ozon FBS — этикетки + Лист подбора",
    page_icon="📦",
    layout="wide"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Основной шрифт + резерв
FONT_CANDIDATES = [
    ("RobotoFull", os.path.join(BASE_DIR, "Roboto_Full_Final.ttf")),
    ("OzonFontFix", os.path.join(BASE_DIR, "OzonFont_Fix.ttf")),
]

FONT_NAME = None
FONT_PATH = None


# ============================================================
# ЗАГРУЗКА ШРИФТА
# ============================================================

def load_font():
    """
    Приоритет:
    1. Roboto_Full_Final.ttf
    2. OzonFont_Fix.ttf

    DejaVu намеренно НЕ используется.
    """

    global FONT_NAME, FONT_PATH

    errors = []

    for name, path in FONT_CANDIDATES:
        if not os.path.exists(path):
            errors.append(f"{os.path.basename(path)} — файл не найден")
            continue

        try:
            pdfmetrics.registerFont(TTFont(name, path))
            FONT_NAME = name
            FONT_PATH = path
            return True, f"{name}: {path}"

        except Exception as e:
            errors.append(
                f"{os.path.basename(path)} — ошибка загрузки: {e}"
            )

    FONT_NAME = "Helvetica"
    FONT_PATH = None

    return False, "\n".join(errors)


FONT_OK, FONT_MESSAGE = load_font()


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================

def normalize_text(value):
    if value is None:
        return ""

    s = str(value)

    replacements = {
        "\u00a0": " ",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
        "–": "-",
        "—": "-",
        "−": "-",
    }

    for a, b in replacements.items():
        s = s.replace(a, b)

    return s.strip()


def normalize_order(value):
    """
    Приводит номер отправления к единому виду.

    Пример:
    87180955-0554-25
    87180955 0554 25
    87180955055425

    Сохраняем также нормализованный вариант только из цифр.
    """

    s = normalize_text(value)

    if not s:
        return ""

    s = s.replace(" ", "")

    m = re.search(
        r"(\d{6,15})[-\s]*(\d{2,8})[-\s]*(\d{1,8})",
        s
    )

    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    digits = re.sub(r"\D", "", s)

    return digits


def order_digits(value):
    return re.sub(r"\D", "", normalize_text(value))


def normalize_key(value):
    """
    Нормализация 4-значного ключа.
    """

    s = normalize_text(value)

    digits = re.sub(r"\D", "", s)

    if len(digits) >= 4:
        return digits[-4:]

    return digits.zfill(4)


# ============================================================
# ПОИСК НОМЕРОВ ОТПРАВЛЕНИЙ
# ============================================================

ORDER_REGEX = re.compile(
    r"\d{6,15}\s*[-–—]?\s*\d{2,8}\s*[-–—]?\s*\d{1,8}"
)


def find_orders(text):
    """
    Возвращает номера отправлений, найденные в тексте.
    """

    text = normalize_text(text)

    result = []

    for m in ORDER_REGEX.finditer(text):
        raw = m.group(0)

        order = normalize_order(raw)

        if order:
            result.append(order)

    # Убираем дубли, сохраняя порядок
    seen = set()
    out = []

    for x in result:
        if x not in seen:
            seen.add(x)
            out.append(x)

    return out


# ============================================================
# ПОИСК 4-ЗНАЧНЫХ КЛЮЧЕЙ
# ============================================================

def extract_four_digit_tokens(text):
    """
    Все самостоятельные 4-значные числа.
    """

    text = normalize_text(text)

    found = re.findall(r"(?<!\d)(\d{4})(?!\d)", text)

    result = []

    for x in found:
        if x not in result:
            result.append(x)

    return result


def extract_long_numeric_tokens(text):
    """
    Находит длинные цифровые последовательности.
    У новых этикеток последние 4 цифры часто являются
    ключом для Листа подбора.
    """

    text = normalize_text(text)

    found = re.findall(r"\d{7,30}", text)

    result = []

    for x in found:
        if x not in result:
            result.append(x)

    return result


def find_label_keys(text):
    """
    Определяет возможные 4-значные ключи новой этикетки.

    Приоритет:
    1. последние 4 цифры длинного цифрового кода;
    2. самостоятельные 4-значные числа;
    3. варианты после OCR-искажений.

    Примеры:
        II5010320 2549 -> 2549
        II50103202549  -> 2549
    """

    text = normalize_text(text)

    keys = []

    # --------------------------------------------------------
    # 1. Длинные цифровые последовательности
    # --------------------------------------------------------

    long_numbers = extract_long_numeric_tokens(text)

    for num in long_numbers:
        if len(num) >= 8:
            key = num[-4:]

            if key not in keys:
                keys.append(key)

    # --------------------------------------------------------
    # 2. Самостоятельные 4 цифры
    # --------------------------------------------------------

    for key in extract_four_digit_tokens(text):
        if key not in keys:
            keys.append(key)

    # --------------------------------------------------------
    # 3. OCR-варианты
    # --------------------------------------------------------

    variants = [
        text,
        text.replace("II", ""),
        text.replace("Il", ""),
        text.replace("ll", ""),
        text.replace("1I", ""),
        text.replace("I1", ""),
    ]

    for variant in variants:
        for num in re.findall(r"\d{7,30}", variant):
            key = num[-4:]

            if key not in keys:
                keys.append(key)

    return keys


# ============================================================
# ЧТЕНИЕ API XLSX / CSV
# ============================================================

def read_api_file(uploaded_file):
    """
    Читает XLSX / XLS / CSV.
    """

    name = uploaded_file.name.lower()

    try:

        if name.endswith(".xlsx") or name.endswith(".xls"):
            df = pd.read_excel(uploaded_file)

        elif name.endswith(".csv"):
            raw = uploaded_file.getvalue()

            try:
                text = raw.decode("utf-8-sig")
            except Exception:
                text = raw.decode("cp1251", errors="replace")

            try:
                df = pd.read_csv(io.StringIO(text))
            except Exception:
                df = pd.read_csv(
                    io.StringIO(text),
                    sep=";"
                )

        else:
            raise ValueError(
                "Поддерживаются только XLSX, XLS и CSV"
            )

        # Нормализуем названия колонок
        df.columns = [
            normalize_text(str(c))
            for c in df.columns
        ]

        return df

    except Exception as e:
        raise RuntimeError(
            f"Ошибка чтения API-файла: {e}"
        )


# ============================================================
# ПОИСК КОЛОНОК API
# ============================================================

def find_column(df, variants):
    """
    Ищет колонку по нескольким возможным названиям.
    """

    columns = list(df.columns)

    normalized_columns = {
        normalize_text(str(c)).lower(): c
        for c in columns
    }

    # Сначала точное совпадение
    for variant in variants:

        v = normalize_text(variant).lower()

        if v in normalized_columns:
            return normalized_columns[v]

    # Потом частичное
    for col in columns:

        col_norm = normalize_text(str(col)).lower()

        for variant in variants:

            v = normalize_text(variant).lower()

            if v in col_norm:
                return col

    return None


def build_api_index(df):
    """
    Строит индекс:

        номер отправления ->
            {
                order,
                article,
                name,
                qty
            }
    """

    order_col = find_column(
        df,
        [
            "Номер отправления",
            "Номер отправки",
            "posting number",
            "posting_number",
            "Номер заказа",
            "Отправление",
            "Заказ",
            "Номер заказа/отправления",
        ]
    )

    article_col = find_column(
        df,
        [
            "Артикул продавца",
            "Артикул",
            "offer_id",
            "Offer ID",
            "Код товара",
            "Seller SKU",
        ]
    )

    name_col = find_column(
        df,
        [
            "Название товара",
            "Название",
            "Наименование",
            "Товар",
            "Наименование товара",
            "Product name",
        ]
    )

    qty_col = find_column(
        df,
        [
            "Количество",
            "Кол-во",
            "Колво",
            "quantity",
            "Qty",
            "Количество товара",
        ]
    )

    if not order_col:
        raise ValueError(
            "Не найдена колонка с номером отправления.\n\n"
            f"Найдены колонки:\n{list(df.columns)}"
        )

    index = {}

    for _, row in df.iterrows():

        raw_order = row.get(order_col, "")

        order = normalize_order(raw_order)

        if not order:
            continue

        article = (
            row.get(article_col, "")
            if article_col
            else ""
        )

        name = (
            row.get(name_col, "")
            if name_col
            else ""
        )

        qty = (
            row.get(qty_col, "")
            if qty_col
            else ""
        )

        article = normalize_text(article)
        name = normalize_text(name)
        qty = normalize_text(qty)

        # Excel может отдавать 1.0
        if re.fullmatch(r"\d+\.0", qty):
            qty = qty[:-2]

        index[order] = {
            "order": order,
            "article": article if article else "-",
            "name": name if name else "НЕ НАЙДЕНО",
            "qty": qty if qty else "?",
        }

    return index, {
        "order_col": order_col,
        "article_col": article_col,
        "name_col": name_col,
        "qty_col": qty_col,
    }


# ============================================================
# ИЗВЛЕЧЕНИЕ СЛОВ PDF С КООРДИНАТАМИ
# ============================================================

def extract_pdf_words(pdf_bytes):
    """
    Возвращает:

    [
        {
            page: 0,
            words: [...]
        }
    ]
    """

    result = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:

        for page_number, page in enumerate(pdf.pages):

            try:
                words = page.extract_words(
                    keep_blank_chars=False,
                    use_text_flow=True
                )
            except Exception:
                words = []

            result.append({
                "page": page_number,
                "words": words
            })

    return result


def word_text(word):
    return normalize_text(
        word.get("text", "")
    )


# ============================================================
# ГРУППИРОВКА WORDS В СТРОКИ
# ============================================================

def group_words_into_lines(words, tolerance=5):
    """
    Группирует слова по вертикальной координате.
    """

    if not words:
        return []

    sorted_words = sorted(
        words,
        key=lambda w: (
            float(w.get("top", 0)),
            float(w.get("x0", 0))
        )
    )

    lines = []

    for word in sorted_words:

        top = float(word.get("top", 0))

        target = None

        for line in lines:

            if abs(top - line["top"]) <= tolerance:
                target = line
                break

        if target is None:

            target = {
                "top": top,
                "words": []
            }

            lines.append(target)

        target["words"].append(word)

    for line in lines:
        line["words"].sort(
            key=lambda w: float(w.get("x0", 0))
        )

        line["text"] = " ".join(
            word_text(w)
            for w in line["words"]
            if word_text(w)
        )

    return lines


# ============================================================
# КЛЮЧИ В СТРОКЕ
# ============================================================

def keys_from_line(line_text):
    """
    Ключи из одной строки.
    """

    result = []

    # длинные номера
    for num in extract_long_numeric_tokens(line_text):

        key = num[-4:]

        if key not in result:
            result.append(key)

    # самостоятельные 4 цифры
    for key in extract_four_digit_tokens(line_text):

        if key not in result:
            result.append(key)

    return result


# ============================================================
# ПОИСК МАППИНГА НА ЛИСТЕ ПОДБОРА
# ============================================================

def build_picklist_mapping(pdf_bytes):
    """
    Строит:

        4-значный ключ -> номер отправления

    На листе подбора обычно рядом находятся:

        2549
        87180955-0554-25

    либо:

        II50103202549
        87180955-0554-25
    """

    mapping_candidates = defaultdict(set)

    diagnostics = []

    pages_data = extract_pdf_words(pdf_bytes)

    for page_data in pages_data:

        page_number = page_data["page"]
        words = page_data["words"]

        lines = group_words_into_lines(words)

        page_text = "\n".join(
            line["text"]
            for line in lines
        )

        orders_on_page = find_orders(page_text)

        # ----------------------------------------------------
        # Основной способ:
        # номер заказа + ключ в одной / соседней строке
        # ----------------------------------------------------

        for i, line in enumerate(lines):

            orders = find_orders(line["text"])

            if not orders:
                continue

            nearby_lines = []

            for j in range(
                max(0, i - 2),
                min(len(lines), i + 3)
            ):
                nearby_lines.append(lines[j])

            keys = []

            for nearby in nearby_lines:

                for key in keys_from_line(
                    nearby["text"]
                ):

                    if key not in keys:
                        keys.append(key)

            for order in orders:

                for key in keys:

                    mapping_candidates[key].add(
                        order
                    )

        # ----------------------------------------------------
        # Способ 2:
        # пространственный поиск
        # ----------------------------------------------------

        order_words = []

        for word in words:

            text = word_text(word)

            if not text:
                continue

            found_orders = find_orders(text)

            if found_orders:

                for order in found_orders:

                    order_words.append(
                        {
                            "order": order,
                            "word": word
                        }
                    )

        four_digit_words = []

        for word in words:

            text = word_text(word)

            if re.fullmatch(
                r"\d{4}",
                text
            ):

                four_digit_words.append(
                    {
                        "key": text,
                        "word": word
                    }
                )

        for ow in order_words:

            ow_word = ow["word"]

            ox = float(ow_word.get("x0", 0))
            oy = float(ow_word.get("top", 0))

            candidates = []

            for kw in four_digit_words:

                kw_word = kw["word"]

                kx = float(kw_word.get("x0", 0))
                ky = float(kw_word.get("top", 0))

                dy = abs(ky - oy)
                dx = abs(kx - ox)

                # Обычно номер и ключ находятся рядом
                if dy <= 80 and dx <= 350:

                    distance = dy * 2 + dx

                    candidates.append(
                        (
                            distance,
                            kw["key"]
                        )
                    )

            candidates.sort(
                key=lambda x: x[0]
            )

            # Берём ближайшие до 3 кандидатов
            for _, key in candidates[:3]:

                mapping_candidates[key].add(
                    ow["order"]
                )

        # ----------------------------------------------------
        # Способ 3:
        # если на странице ровно один заказ,
        # пытаемся связать его с ключами страницы
        # ----------------------------------------------------

        if len(orders_on_page) == 1:

            only_order = orders_on_page[0]

            page_keys = []

            for key in find_label_keys(page_text):

                if key not in page_keys:
                    page_keys.append(key)

            # Если ключей немного — это хороший кандидат
            if 1 <= len(page_keys) <= 5:

                for key in page_keys:

                    mapping_candidates[key].add(
                        only_order
                    )

        diagnostics.append({
            "page": page_number + 1,
            "orders": orders_on_page,
            "keys": find_label_keys(page_text),
            "text": page_text
        })

    # --------------------------------------------------------
    # Формируем окончательный индекс.
    #
    # Если один ключ связан с несколькими заказами —
    # он считается неоднозначным.
    # --------------------------------------------------------

    mapping = {}

    ambiguous = {}

    for key, orders in mapping_candidates.items():

        if len(orders) == 1:

            mapping[key] = next(iter(orders))

        elif len(orders) > 1:

            ambiguous[key] = sorted(
                orders
            )

    return mapping, ambiguous, diagnostics


# ============================================================
# ПОИСК ДАННЫХ ЭТИКЕТКИ
# ============================================================

def get_page_text(page):
    try:
        return normalize_text(
            page.extract_text() or ""
        )
    except Exception:
        return ""


def find_direct_order(page_text):
    orders = find_orders(page_text)

    if not orders:
        return None

    # Если несколько — берём первый
    return orders[0]


def choose_key_from_page(page_text):
    """
    Определяем ключ для новой этикетки.

    Стараемся не брать случайные 4 цифры,
    если есть длинный цифровой код.
    """

    long_numbers = extract_long_numeric_tokens(
        page_text
    )

    candidates = []

    for num in long_numbers:

        if len(num) >= 8:

            key = num[-4:]

            if key not in candidates:
                candidates.append(key)

    if candidates:
        return candidates[0]

    four_digits = extract_four_digit_tokens(
        page_text
    )

    # Убираем типичные числа, которые чаще бывают
    # датами/временем/весом и т.п.
    excluded = {
        "2026",
        "2025",
        "2024",
        "2023",
        "0000",
    }

    filtered = [
        x
        for x in four_digits
        if x not in excluded
    ]

    if filtered:
        return filtered[-1]

    return None


# ============================================================
# СОЗДАНИЕ ИНФОРМАЦИОННОЙ СТРАНИЦЫ
# ============================================================

def create_info_page(
    order,
    article,
    name,
    qty,
    page_size
):
    """
    Создаёт одну страницу с информацией.

    ВАЖНО:
    SKU здесь НЕ выводится.
    """

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=page_size
    )

    width, height = page_size

    # --------------------------------------------------------
    # Шрифт
    # --------------------------------------------------------

    if FONT_NAME:
        font_name = FONT_NAME
    else:
        font_name = "Helvetica"

    # --------------------------------------------------------
    # Размеры
    # --------------------------------------------------------

    left = 45

    y = height - 80

    # --------------------------------------------------------
    # Заголовок
    # --------------------------------------------------------

    c.setFont(
        font_name,
        24
    )

    c.drawString(
        left,
        y,
        "ИНФОРМАЦИЯ ПО ОТПРАВЛЕНИЮ"
    )

    y -= 60

    # --------------------------------------------------------
    # Поля
    # --------------------------------------------------------

    fields = [
        ("Заказ:", order),
        ("Арт:", article),
        ("Название:", name),
        ("КОЛ-ВО:", qty),
    ]

    label_size = 17
    value_size = 17

    for label, value in fields:

        c.setFont(
            font_name,
            label_size
        )

        c.drawString(
            left,
            y,
            label
        )

        c.setFont(
            font_name,
            value_size
        )

        # Значение справа от label
        label_width = pdfmetrics.stringWidth(
            label,
            font_name,
            label_size
        )

        value_x = left + label_width + 15

        # Для длинного названия переносим строку
        if label == "Название:":

            max_width = width - value_x - 45

            value = str(value)

            # Если короткое
            if pdfmetrics.stringWidth(
                value,
                font_name,
                value_size
            ) <= max_width:

                c.drawString(
                    value_x,
                    y,
                    value
                )

                y -= 45

            else:

                words = value.split()

                line = ""

                first_line = True

                for word in words:

                    test = (
                        line + " " + word
                    ).strip()

                    if pdfmetrics.stringWidth(
                        test,
                        font_name,
                        value_size
                    ) <= max_width:

                        line = test

                    else:

                        if line:

                            c.drawString(
                                value_x if first_line else left,
                                y,
                                line
                            )

                            y -= 28

                            first_line = False

                        line = word

                if line:

                    c.drawString(
                        value_x if first_line else left,
                        y,
                        line
                    )

                    y -= 45

        else:

            c.drawString(
                value_x,
                y,
                str(value)
            )

            y -= 50

    # --------------------------------------------------------
    # Большое количество
    # --------------------------------------------------------

    y -= 20

    c.setFont(
        font_name,
        38
    )

    c.drawString(
        left,
        y,
        f"КОЛ-ВО: {qty}"
    )

    c.showPage()
    c.save()

    buffer.seek(0)

    return buffer.getvalue()


# ============================================================
# ОСНОВНАЯ ОБРАБОТКА ЭТИКЕТОК
# ============================================================

def process_labels(
    labels_bytes,
    api_index,
    picklist_mapping
):
    """
    На каждую исходную страницу:

        1. оригинальная этикетка
        2. информационная страница

    Итого всегда 2 страницы на отправление.
    """

    reader = PdfReader(
        io.BytesIO(labels_bytes)
    )

    writer = PdfWriter()

    stats = {
        "total": len(reader.pages),
        "direct": 0,
        "new": 0,
        "found_api": 0,
        "not_found_api": 0,
        "not_recognized": 0,
    }

    diagnostics = []

    for page_index, source_page in enumerate(
        reader.pages
    ):

        # ----------------------------------------------------
        # Добавляем оригинальную страницу БЕЗ изменений
        # ----------------------------------------------------

        writer.add_page(source_page)

        # ----------------------------------------------------
        # Получаем текст
        # ----------------------------------------------------

        try:
            page_text = source_page.extract_text() or ""
        except Exception:
            page_text = ""

        page_text = normalize_text(
            page_text
        )

        # ----------------------------------------------------
        # 1. Прямой номер отправления
        # ----------------------------------------------------

        order = find_direct_order(
            page_text
        )

        key = None
        recognition_type = None

        if order:

            recognition_type = "Прямой номер"

            stats["direct"] += 1

        else:

            # ------------------------------------------------
            # 2. Новая этикетка
            # ------------------------------------------------

            key = choose_key_from_page(
                page_text
            )

            if key:

                recognition_type = (
                    "Новая этикетка / 4 цифры"
                )

                stats["new"] += 1

                order = picklist_mapping.get(
                    key
                )

        # ----------------------------------------------------
        # Получаем данные API
        # ----------------------------------------------------

        api_data = None

        if order:

            api_data = api_index.get(
                normalize_order(order)
            )

        if api_data:

            stats["found_api"] += 1

            info_order = api_data["order"]
            article = api_data["article"]
            name = api_data["name"]
            qty = api_data["qty"]

        else:

            stats["not_found_api"] += 1

            if order:

                info_order = order
                article = "-"
                name = "НЕ НАЙДЕНО"
                qty = "?"

            else:

                stats["not_recognized"] += 1

                info_order = "НЕ РАСПОЗНАН"
                article = "-"
                name = "НЕ НАЙДЕНО"
                qty = "?"

        # ----------------------------------------------------
        # Информационная страница
        # ----------------------------------------------------

        page_width = float(
            source_page.mediabox.width
        )

        page_height = float(
            source_page.mediabox.height
        )

        page_size = (
            page_width,
            page_height
        )

        info_pdf = create_info_page(
            info_order,
            article,
            name,
            qty,
            page_size
        )

        info_reader = PdfReader(
            io.BytesIO(info_pdf)
        )

        writer.add_page(
            info_reader.pages[0]
        )

        # ----------------------------------------------------
        # Диагностика
        # ----------------------------------------------------

        diagnostics.append({
            "page": page_index + 1,
            "type": recognition_type or "НЕ РАСПОЗНАН",
            "key": key or "",
            "order": order or "",
            "api_found": bool(api_data),
            "article": article,
            "name": name,
            "qty": qty,
            "text": page_text,
        })

    # --------------------------------------------------------
    # Запись
    # --------------------------------------------------------

    output = io.BytesIO()

    writer.write(output)

    output.seek(0)

    return output.getvalue(), stats, diagnostics


# ============================================================
# UI
# ============================================================

st.title(
    "📦 Ozon FBS — этикетки + Лист подбора"
)

st.caption(
    "На каждую этикетку создаётся ровно 2 страницы: "
    "оригинальная этикетка + информационная страница."
)


# ============================================================
# ИНФОРМАЦИЯ О ШРИФТЕ
# ============================================================

st.subheader("🔤 Шрифт")

if FONT_OK:

    st.success(
        f"Используется основной шрифт: "
        f"`{os.path.basename(FONT_PATH)}`"
    )

    st.caption(
        "Приоритет: Roboto_Full_Final.ttf → "
        "OzonFont_Fix.ttf"
    )

else:

    st.error(
        "Не удалось загрузить Roboto_Full_Final.ttf "
        "или OzonFont_Fix.ttf."
    )

    st.code(
        FONT_MESSAGE
    )

    st.warning(
        "Проверьте, что эти файлы находятся рядом с app.py."
    )


# ============================================================
# ФАЙЛЫ
# ============================================================

st.subheader("1. Файлы")

labels_file = st.file_uploader(
    "📄 Оригинальные Ozon этикетки PDF",
    type=["pdf"],
    key="labels_pdf"
)

api_file = st.file_uploader(
    "📊 Выгрузка Ozon API XLSX / CSV",
    type=["xlsx", "xls", "csv"],
    key="api_file"
)

picklist_file = st.file_uploader(
    "📋 Лист подбора PDF",
    type=["pdf"],
    key="picklist_pdf"
)


# ============================================================
# ЗАПУСК
# ============================================================

if st.button(
    "🚀 ОБРАБОТАТЬ",
    type="primary",
    use_container_width=True
):

    if not labels_file:
        st.error(
            "Загрузите оригинальные этикетки PDF."
        )
        st.stop()

    if not api_file:
        st.error(
            "Загрузите XLSX/CSV выгрузку Ozon API."
        )
        st.stop()

    if not picklist_file:
        st.error(
            "Загрузите PDF Лист подбора."
        )
        st.stop()

    # --------------------------------------------------------
    # Читаем файлы
    # --------------------------------------------------------

    labels_bytes = labels_file.getvalue()
    api_bytes = api_file.getvalue()
    picklist_bytes = picklist_file.getvalue()

    # --------------------------------------------------------
    # API
    # --------------------------------------------------------

    with st.spinner(
        "Читаю выгрузку Ozon API..."
    ):

        try:

            class UploadedMemoryFile:
                def __init__(self, name, data):
                    self.name = name
                    self._data = data

                def getvalue(self):
                    return self._data

            api_memory = UploadedMemoryFile(
                api_file.name,
                api_bytes
            )

            df = read_api_file(
                api_memory
            )

            api_index, api_columns = build_api_index(
                df
            )

        except Exception as e:

            st.error(
                f"Ошибка API-файла: {e}"
            )

            st.stop()

    # --------------------------------------------------------
    # Лист подбора
    # --------------------------------------------------------

    with st.spinner(
        "Анализирую Лист подбора..."
    ):

        try:

            picklist_mapping, ambiguous, picklist_diag = (
                build_picklist_mapping(
                    picklist_bytes
                )
            )

        except Exception as e:

            st.error(
                f"Ошибка анализа Листа подбора: {e}"
            )

            st.stop()

    # --------------------------------------------------------
    # Этикетки
    # --------------------------------------------------------

    with st.spinner(
        "Обрабатываю этикетки..."
    ):

        try:

            output_bytes, stats, diagnostics = (
                process_labels(
                    labels_bytes,
                    api_index,
                    picklist_mapping
                )
            )

        except Exception as e:

            st.error(
                f"Ошибка обработки PDF: {e}"
            )

            st.exception(e)

            st.stop()

    # ========================================================
    # РЕЗУЛЬТАТ
    # ========================================================

    st.success(
        "Готово!"
    )

    # --------------------------------------------------------
    # Статистика
    # --------------------------------------------------------

    st.subheader(
        "📊 Результат"
    )

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(
            "Этикеток",
            stats["total"]
        )

    with col2:
        st.metric(
            "Прямой номер",
            stats["direct"]
        )

    with col3:
        st.metric(
            "Новые",
            stats["new"]
        )

    with col4:
        st.metric(
            "Найдено API",
            stats["found_api"]
        )

    with col5:
        st.metric(
            "Не найдено",
            stats["not_found_api"]
        )

    st.info(
        f"В результате будет "
        f"{stats['total'] * 2} страниц."
    )

    # --------------------------------------------------------
    # Информация API
    # --------------------------------------------------------

    with st.expander(
        "🔎 Колонки, найденные в API"
    ):

        st.write(
            api_columns
        )

        st.write(
            f"Записей API: {len(df)}"
        )

    # --------------------------------------------------------
    # Маппинг Листа подбора
    # --------------------------------------------------------

    with st.expander(
        f"📋 Маппинг Листа подбора "
        f"({len(picklist_mapping)} ключей)"
    ):

        if picklist_mapping:

            mapping_rows = []

            for key, order in sorted(
                picklist_mapping.items()
            ):

                mapping_rows.append({
                    "Ключ": key,
                    "Номер отправления": order
                })

            st.dataframe(
                pd.DataFrame(mapping_rows),
                use_container_width=True,
                hide_index=True
            )

        else:

            st.warning(
                "Не найдено ни одного соответствия "
                "4 цифры → номер отправления."
            )

        if ambiguous:

            st.warning(
                f"Неоднозначных ключей: "
                f"{len(ambiguous)}"
            )

            ambiguous_rows = []

            for key, orders in ambiguous.items():

                ambiguous_rows.append({
                    "Ключ": key,
                    "Варианты заказов": ", ".join(
                        orders
                    )
                })

            st.dataframe(
                pd.DataFrame(ambiguous_rows),
                use_container_width=True,
                hide_index=True
            )

    # --------------------------------------------------------
    # Нераспознанные
    # --------------------------------------------------------

    unresolved = [
        x
        for x in diagnostics
        if not x["order"]
        or not x["api_found"]
    ]

    with st.expander(
        f"⚠️ Диагностика проблемных этикеток "
        f"({len(unresolved)})"
    ):

        if not unresolved:

            st.success(
                "Все этикетки распознаны и найдены в API."
            )

        else:

            for item in unresolved:

                st.markdown(
                    f"### Страница {item['page']}"
                )

                st.write({
                    "Тип": item["type"],
                    "Ключ": item["key"],
                    "Заказ": item["order"],
                    "API найден": item["api_found"],
                    "Артикул": item["article"],
                    "Количество": item["qty"],
                })

                st.text_area(
                    "Извлечённый текст",
                    item["text"],
                    height=180,
                    key=f"diag_{item['page']}"
                )

                st.divider()

    # --------------------------------------------------------
    # Все этикетки
    # --------------------------------------------------------

    with st.expander(
        "🔍 Полная диагностика всех этикеток"
    ):

        diagnostic_rows = []

        for item in diagnostics:

            diagnostic_rows.append({
                "Страница": item["page"],
                "Тип": item["type"],
                "Ключ": item["key"],
                "Заказ": item["order"],
                "API": "ДА" if item["api_found"] else "НЕТ",
                "Артикул": item["article"],
                "Количество": item["qty"],
            })

        st.dataframe(
            pd.DataFrame(diagnostic_rows),
            use_container_width=True,
            hide_index=True
        )

    # --------------------------------------------------------
    # Скачать
    # --------------------------------------------------------

    st.subheader(
        "⬇️ Скачать результат"
    )

    st.download_button(
        label="📥 Скачать готовый PDF",
        data=output_bytes,
        file_name="Ozon_FBS_Этикетки_2_страницы.pdf",
        mime="application/pdf",
        type="primary",
        use_container_width=True
    )
