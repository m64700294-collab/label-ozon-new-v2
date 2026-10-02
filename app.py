import streamlit as st
import re
import os
from io import BytesIO
from collections import defaultdict

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader


# ============================================================
# НАСТРОЙКИ
# ============================================================

st.set_page_config(
    page_title="Ozon FBS — Обработка этикеток",
    page_icon="📦",
    layout="wide"
)

APP_TITLE = "📦 Ozon FBS — обработка этикеток"

FONT_NAME = "DejaVuSans"
FONT_PATH = "/tmp/DejaVuSans.ttf"
FONT_URL = (
    "https://github.com/dejavu-fonts/ttf-dejavu/raw/master/"
    "ttf/DejaVuSans.ttf"
)


# ============================================================
# ЗАГРУЗКА ШРИФТА
# ============================================================

def load_font():
    """
    Загружает Unicode-шрифт для русских букв.
    """
    if os.path.exists(FONT_PATH):
        try:
            pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))
            return True
        except Exception:
            pass

    try:
        import requests

        response = requests.get(
            FONT_URL,
            timeout=30
        )

        if response.ok:
            with open(FONT_PATH, "wb") as f:
                f.write(response.content)

            pdfmetrics.registerFont(
                TTFont(FONT_NAME, FONT_PATH)
            )

            return True

    except Exception:
        pass

    return False


FONT_OK = load_font()


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================

def normalize_order(value):
    """
    Нормализация номера отправления.
    Пример:
    87180955-0554-25
    87180955 - 0554 - 25
    """
    if value is None:
        return ""

    s = str(value).strip()

    s = s.replace("–", "-")
    s = s.replace("—", "-")
    s = re.sub(r"\s+", "", s)

    return s.lower()


def normalize_label_key(value):
    """
    Ключ этикетки = последние 4 цифры.

    Например:
    II5010320 2549
    II50103202549
    -> 2549
    """

    if value is None:
        return ""

    s = str(value)

    digits = re.findall(r"\d", s)

    if len(digits) < 4:
        return ""

    return "".join(digits[-4:])


# ============================================================
# ПОИСК НОМЕРОВ ОТПРАВЛЕНИЙ
# ============================================================

ORDER_PATTERN = re.compile(
    r"\b\d{6,15}\s*-\s*\d{2,6}\s*-\s*\d+\b"
)


def find_orders(text):
    """
    Находит номера отправлений Ozon.
    """

    if not text:
        return []

    matches = ORDER_PATTERN.findall(text)

    result = []

    for item in matches:
        order = normalize_order(item)

        if order and order not in result:
            result.append(order)

    return result


# ============================================================
# ПОИСК КЛЮЧЕЙ НОВЫХ ЭТИКЕТОК
# ============================================================

LABEL_PREFIX_PATTERN = re.compile(
    r"""
    (?:
        ii|
        ll|
        1i|
        i1|
        11|
        ии|
        иi|
        iи
    )
    \s*
    ([0-9\s]{6,25})
    """,
    re.IGNORECASE | re.VERBOSE
)


def find_label_keys(text):
    """
    Ищет последние 4 цифры в кодах новых этикеток.

    Поддерживает варианты OCR:
      II5010320 2549
      II50103202549
      ll5010320 2549
      115010320 2549
    """

    if not text:
        return []

    result = []

    # --------------------------------------------------------
    # Вариант 1:
    # II5010320 2549
    # --------------------------------------------------------

    patterns = [
        re.compile(
            r"(?:ii|ll|1i|i1|11|ии|иi|iи)"
            r"\s*\d{6,15}\s+(\d{4})\b",
            re.IGNORECASE
        ),

        re.compile(
            r"(?:ii|ll|1i|i1|11|ии|иi|iи)"
            r"\s*\d{10,20}\b",
            re.IGNORECASE
        ),
    ]

    for pattern in patterns:

        for match in pattern.finditer(text):

            if match.lastindex:
                value = match.group(match.lastindex)
            else:
                value = match.group(0)

            key = normalize_label_key(value)

            if key and key not in result:
                result.append(key)

    # --------------------------------------------------------
    # Вариант 2:
    # OCR мог разнести код по строкам
    # --------------------------------------------------------

    compact = re.sub(
        r"[ \t\r\n]+",
        " ",
        text
    )

    for pattern in patterns:

        for match in pattern.finditer(compact):

            if match.lastindex:
                value = match.group(match.lastindex)
            else:
                value = match.group(0)

            key = normalize_label_key(value)

            if key and key not in result:
                result.append(key)

    # --------------------------------------------------------
    # Вариант 3:
    # ищем 4 цифры после OCR-префикса
    # --------------------------------------------------------

    fallback = re.compile(
        r"(?:ii|ll|1i|i1|11|ии|иi|iи)"
        r"[^\d]{0,5}"
        r"(?:\d[^\s\-]{0,20}\s+)?"
        r"(\d{4})\b",
        re.IGNORECASE
    )

    for match in fallback.finditer(compact):

        key = normalize_label_key(
            match.group(1)
        )

        if key and key not in result:
            result.append(key)

    return result


# ============================================================
# НОРМАЛИЗАЦИЯ НАЗВАНИЙ КОЛОНОК
# ============================================================

def normalize_column_name(value):
    if value is None:
        return ""

    s = str(value).strip().lower()

    s = s.replace("ё", "е")

    s = re.sub(
        r"[\s\n\r\t]+",
        " ",
        s
    )

    return s


def find_column(columns, variants):
    """
    Ищет колонку по нескольким возможным названиям.
    """

    normalized = {
        normalize_column_name(c): c
        for c in columns
    }

    # точное совпадение
    for variant in variants:

        v = normalize_column_name(variant)

        if v in normalized:
            return normalized[v]

    # частичное совпадение
    for column in columns:

        c = normalize_column_name(column)

        for variant in variants:

            v = normalize_column_name(variant)

            if v in c:
                return column

    return None


# ============================================================
# ЧТЕНИЕ EXCEL / CSV
# ============================================================

def read_excel_file(uploaded_file):
    import pandas as pd

    filename = uploaded_file.name.lower()

    if filename.endswith(".csv"):

        raw = uploaded_file.getvalue()

        encodings = [
            "utf-8-sig",
            "utf-8",
            "cp1251"
        ]

        last_error = None

        for encoding in encodings:

            try:
                return pd.read_csv(
                    BytesIO(raw),
                    encoding=encoding,
                    sep=None,
                    engine="python"
                )
            except Exception as e:
                last_error = e

        raise last_error

    return pd.read_excel(
        uploaded_file
    )


# ============================================================
# ПОДГОТОВКА API-ИНДЕКСА
# ============================================================

def build_api_index(df):

    columns = list(df.columns)

    order_col = find_column(
        columns,
        [
            "Номер отправления",
            "Номер отправки",
            "Номер заказа",
            "posting number",
            "posting_number",
            "Отправление",
            "Отправление номер",
            "Заказ"
        ]
    )

    article_col = find_column(
        columns,
        [
            "Артикул продавца",
            "Артикул",
            "offer_id",
            "Offer ID",
            "Артикул товара",
            "Код товара"
        ]
    )

    name_col = find_column(
        columns,
        [
            "Название товара",
            "Название",
            "Товар",
            "Наименование",
            "Название продукта",
            "Product name",
            "Наименование товара"
        ]
    )

    qty_col = find_column(
        columns,
        [
            "Количество",
            "Кол-во",
            "Колво",
            "quantity",
            "Qty",
            "Количество товара"
        ]
    )

    if not order_col:
        raise ValueError(
            "Не найдена колонка с номером отправления."
        )

    if not article_col:
        st.warning(
            "⚠️ Не найдена колонка «Артикул продавца»."
        )

    if not name_col:
        st.warning(
            "⚠️ Не найдена колонка «Название товара»."
        )

    if not qty_col:
        st.warning(
            "⚠️ Не найдена колонка «Количество»."
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
            value = row.get(
                article_col,
                ""
            )

            if value is not None:
                article = str(value).strip()

        name = ""

        if name_col:
            value = row.get(
                name_col,
                ""
            )

            if value is not None:
                name = str(value).strip()

        qty = ""

        if qty_col:
            value = row.get(
                qty_col,
                ""
            )

            if value is not None:

                if isinstance(value, float):

                    if value.is_integer():
                        qty = str(int(value))
                    else:
                        qty = str(value)

                else:
                    qty = str(value).strip()

        index[order] = {
            "order": order,
            "article": article,
            "name": name,
            "qty": qty
        }

    return index


# ============================================================
# PDF PICKLIST — ВЫТАСКИВАЕМ СВЯЗИ
# ============================================================

def build_picklist_mapping(pdf_file):

    """
    Строит связь:

        2549 -> 87180955-0554-25

    Основной метод:
    pdfplumber + координаты слов.

    Дополнительный fallback:
    поиск на одной странице.
    """

    mappings = []

    try:
        import pdfplumber

        pdf_file.seek(0)

        with pdfplumber.open(pdf_file) as pdf:

            for page_number, page in enumerate(
                pdf.pages,
                start=1
            ):

                words = page.extract_words(
                    x_tolerance=2,
                    y_tolerance=3,
                    keep_blank_chars=False
                )

                if not words:
                    continue

                # ------------------------------------------------
                # Формируем строки из слов
                # ------------------------------------------------

                lines = []

                for word in words:

                    top = float(
                        word.get("top", 0)
                    )

                    placed = False

                    for line in lines:

                        if abs(
                            line["top"] - top
                        ) <= 4:

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

                # ------------------------------------------------
                # Сортируем строки и слова
                # ------------------------------------------------

                for line in lines:
                    line["words"].sort(
                        key=lambda x: float(
                            x.get("x0", 0)
                        )
                    )

                lines.sort(
                    key=lambda x: x["top"]
                )

                # ------------------------------------------------
                # Проверяем каждую строку
                # ------------------------------------------------

                for i, line in enumerate(lines):

                    line_text = " ".join(
                        str(w.get("text", ""))
                        for w in line["words"]
                    )

                    orders = find_orders(
                        line_text
                    )

                    if not orders:
                        continue

                    # Ищем label key в той же строке
                    label_keys = find_label_keys(
                        line_text
                    )

                    # --------------------------------------------
                    # Если в той же строке нет —
                    # смотрим соседние строки
                    # --------------------------------------------

                    if not label_keys:

                        nearby_indexes = [
                            j
                            for j in range(
                                max(0, i - 2),
                                min(
                                    len(lines),
                                    i + 3
                                )
                            )
                            if j != i
                        ]

                        nearby_text = " ".join(
                            " ".join(
                                str(w.get("text", ""))
                                for w in lines[j]["words"]
                            )
                            for j in nearby_indexes
                        )

                        label_keys = find_label_keys(
                            nearby_text
                        )

                    if not label_keys:
                        continue

                    # ------------------------------------------------
                    # Создаем связи
                    # ------------------------------------------------

                    for order in orders:

                        for label_key in label_keys:

                            mappings.append({
                                "label_key": label_key,
                                "order": order,
                                "page": page_number,
                                "method": "координаты"
                            })

    except Exception as e:

        st.warning(
            "⚠️ Не удалось обработать PDF листа подбора "
            f"через pdfplumber: {e}"
        )

    # ========================================================
    # FALLBACK: ПРОСТО ПО СТРАНИЦЕ
    # ========================================================

    if not mappings:

        try:

            pdf_file.seek(0)

            reader = PdfReader(
                pdf_file
            )

            for page_number, page in enumerate(
                reader.pages,
                start=1
            ):

                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""

                orders = find_orders(text)
                label_keys = find_label_keys(text)

                if len(orders) == 1:

                    for key in label_keys:

                        mappings.append({
                            "label_key": key,
                            "order": orders[0],
                            "page": page_number,
                            "method": "страница"
                        })

        except Exception as e:

            st.warning(
                "⚠️ Fallback обработки листа подбора "
                f"также завершился ошибкой: {e}"
            )

    # ========================================================
    # УДАЛЯЕМ ДУБЛИКАТЫ
    # ========================================================

    unique = {}

    for item in mappings:

        key = (
            item["label_key"],
            item["order"]
        )

        if key not in unique:
            unique[key] = item

    return list(
        unique.values()
    )


# ============================================================
# СОЗДАНИЕ ИНДЕКСА LABEL -> ORDER
# ============================================================

def build_label_index(mappings):

    temp = defaultdict(set)

    for item in mappings:

        label_key = item["label_key"]
        order = item["order"]

        if label_key and order:
            temp[label_key].add(order)

    result = {}

    for label_key, orders in temp.items():

        # Если одному ключу соответствует ровно один заказ
        if len(orders) == 1:

            result[label_key] = list(
                orders
            )[0]

    return result


# ============================================================
# ТЕКСТОВЫЙ ПЕРЕНОС
# ============================================================

def wrap_text(text, max_chars):
    """
    Простой перенос текста для PDF.
    """

    if not text:
        return [""]

    text = str(text)

    words = text.split()

    lines = []
    current = ""

    for word in words:

        if not current:

            current = word

        elif len(current) + 1 + len(word) <= max_chars:

            current += " " + word

        else:

            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines or [""]


# ============================================================
# ИНФОРМАЦИОННАЯ СТРАНИЦА
# ============================================================

def create_info_page(
    width,
    height,
    item=None,
    unresolved=False,
    label_key=None,
    source=""
):

    buffer = BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=(width, height)
    )

    # --------------------------------------------------------
    # ШРИФТ
    # --------------------------------------------------------

    if FONT_OK:
        font = FONT_NAME
    else:
        font = "Helvetica"

    # --------------------------------------------------------
    # НЕ НАЙДЕНО
    # --------------------------------------------------------

    if unresolved or not item:

        c.setFont(
            font,
            13
        )

        c.drawString(
            25,
            height - 40,
            "Заказ: НЕ РАСПОЗНАН"
        )

        c.setFont(
            font,
            11
        )

        c.drawString(
            25,
            height - 65,
            "Арт: -"
        )

        c.drawString(
            25,
            height - 90,
            "Название: НЕ НАЙДЕНО"
        )

        c.drawString(
            25,
            height - 115,
            "КОЛ-ВО: ?"
        )

        if label_key:

            c.setFont(
                font,
                8
            )

            c.drawString(
                25,
                25,
                f"Ключ этикетки: {label_key}"
            )

        c.save()

        buffer.seek(0)

        return PdfReader(
            buffer
        ).pages[0]

    # --------------------------------------------------------
    # НАЙДЕНО
    # --------------------------------------------------------

    order = item.get(
        "order",
        ""
    )

    article = item.get(
        "article",
        ""
    )

    name = item.get(
        "name",
        ""
    )

    qty = item.get(
        "qty",
        ""
    )

    c.setFont(
        font,
        11
    )

    y = height - 40

    # Заказ

    c.drawString(
        25,
        y,
        f"Заказ: {order}"
    )

    y -= 25

    # Артикул

    c.drawString(
        25,
        y,
        f"Арт: {article or '-'}"
    )

    y -= 25

    # Название

    c.drawString(
        25,
        y,
        "Название:"
    )

    y -= 17

    # Длинное название переносим

    name_lines = wrap_text(
        name or "-",
        max_chars=48
    )

    for line in name_lines:

        if y < 55:
            break

        c.setFont(
            font,
            9
        )

        c.drawString(
            25,
            y,
            line
        )

        y -= 14

    # Количество

    y -= 8

    c.setFont(
        font,
        11
    )

    c.drawString(
        25,
        y,
        f"КОЛ-ВО: {qty or '?'}"
    )

    # Небольшая диагностика снизу

    if source:

        c.setFont(
            font,
            7
        )

        c.drawString(
            25,
            25,
            f"Источник: {source}"
        )

    c.save()

    buffer.seek(0)

    return PdfReader(
        buffer
    ).pages[0]


# ============================================================
# ПОИСК ИНФОРМАЦИИ ПО ЗАКАЗУ
# ============================================================

def find_item_by_order(
    order,
    api_index
):

    if not order:
        return None

    normalized = normalize_order(
        order
    )

    return api_index.get(
        normalized
    )


# ============================================================
# ОБРАБОТКА PDF
# ============================================================

def process_pdf(
    labels_file,
    api_index,
    label_index
):

    labels_file.seek(0)

    reader = PdfReader(
        labels_file
    )

    writer = PdfWriter()

    stats = {
        "total": 0,
        "direct": 0,
        "picklist": 0,
        "unresolved": 0
    }

    diagnostics = []

    for page_number, page in enumerate(
        reader.pages,
        start=1
    ):

        stats["total"] += 1

        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""

        # ====================================================
        # 1. СНАЧАЛА ИЩЕМ ОБЫЧНЫЙ НОМЕР ОТПРАВЛЕНИЯ
        # ====================================================

        orders = find_orders(
            text
        )

        item = None
        resolved_order = ""
        source = ""
        used_label_key = ""

        for order in orders:

            candidate = find_item_by_order(
                order,
                api_index
            )

            if candidate:

                item = candidate
                resolved_order = order
                source = "прямой номер"
                break

        # ====================================================
        # 2. ЕСЛИ НЕ НАШЛИ — ИЩЕМ НОВУЮ ЭТИКЕТКУ
        # ====================================================

        if not item:

            label_keys = find_label_keys(
                text
            )

            for label_key in label_keys:

                mapped_order = label_index.get(
                    label_key
                )

                if not mapped_order:
                    continue

                candidate = find_item_by_order(
                    mapped_order,
                    api_index
                )

                if candidate:

                    item = candidate
                    resolved_order = mapped_order
                    used_label_key = label_key
                    source = (
                        "лист подбора"
                    )

                    break

        # ====================================================
        # 3. ВСЕГДА ДОБАВЛЯЕМ ОРИГИНАЛЬНУЮ ЭТИКЕТКУ
        # ====================================================

        writer.add_page(
            page
        )

        # ====================================================
        # 4. ДОБАВЛЯЕМ РОВНО ОДНУ ИНФОРМАЦИОННУЮ СТРАНИЦУ
        # ====================================================

        width = float(
            page.mediabox.width
        )

        height = float(
            page.mediabox.height
        )

        if item:

            writer.add_page(
                create_info_page(
                    width=width,
                    height=height,
                    item=item,
                    unresolved=False,
                    label_key=used_label_key,
                    source=source
                )
            )

            if source == "прямой номер":
                stats["direct"] += 1

            elif source == "лист подбора":
                stats["picklist"] += 1

            diagnostics.append({
                "Страница": page_number,
                "Статус": "НАЙДЕНО",
                "Заказ": resolved_order,
                "Артикул": item.get(
                    "article",
                    ""
                ),
                "Название": item.get(
                    "name",
                    ""
                ),
                "Кол-во": item.get(
                    "qty",
                    ""
                ),
                "Источник": source,
                "Ключ": used_label_key
            })

        else:

            writer.add_page(
                create_info_page(
                    width=width,
                    height=height,
                    item=None,
                    unresolved=True,
                    label_key=(
                        find_label_keys(text)[0]
                        if find_label_keys(text)
                        else ""
                    ),
                    source=""
                )
            )

            stats["unresolved"] += 1

            label_keys = find_label_keys(
                text
            )

            diagnostics.append({
                "Страница": page_number,
                "Статус": "НЕ НАЙДЕНО",
                "Заказ": (
                    orders[0]
                    if orders
                    else ""
                ),
                "Артикул": "",
                "Название": "",
                "Кол-во": "",
                "Источник": "",
                "Ключ": (
                    label_keys[0]
                    if label_keys
                    else ""
                )
            })

    # ========================================================
    # СОХРАНЕНИЕ
    # ========================================================

    output = BytesIO()

    writer.write(
        output
    )

    output.seek(0)

    return (
        output,
        stats,
        diagnostics
    )


# ============================================================
# ИНТЕРФЕЙС
# ============================================================

st.title(
    APP_TITLE
)

st.markdown(
    """
### Как работает обработка

**1. Исходный PDF этикеток**  
Оригинальные страницы остаются без изменений.

**2. XLSX / CSV Ozon API**  
Из него берутся:
- номер отправления;
- артикул продавца;
- название товара;
- количество.

**3. PDF «Лист подбора»**  
Используется для новых этикеток, где на самой этикетке вместо обычного номера находится код вроде:

`II50103202549`

Из него берётся:

`2549`

После этого `2549` связывается с номером отправления на листе подбора.
"""
)


# ============================================================
# ЗАГРУЗКА ФАЙЛОВ
# ============================================================

st.subheader(
    "1. Исходные файлы"
)

labels_file = st.file_uploader(
    "📄 PDF — исходные этикетки Ozon",
    type=["pdf"],
    key="labels_pdf"
)

api_file = st.file_uploader(
    "📊 XLSX / CSV — выгрузка Ozon API",
    type=["xlsx", "xls", "csv"],
    key="api_file"
)

picklist_file = st.file_uploader(
    "📋 PDF — Лист подбора",
    type=["pdf"],
    key="picklist_pdf"
)


# ============================================================
# ПРЕДПРОСМОТР API
# ============================================================

api_index = None

if api_file:

    try:

        df = read_excel_file(
            api_file
        )

        st.success(
            f"✅ API-файл загружен: "
            f"{len(df):,} строк"
        )

        with st.expander(
            "🔎 Посмотреть структуру API-файла"
        ):

            st.write(
                "Колонки:"
            )

            st.write(
                list(df.columns)
            )

            st.dataframe(
                df.head(10),
                use_container_width=True
            )

        api_index = build_api_index(
            df
        )

        st.info(
            f"В индексе API найдено "
            f"отправлений: **{len(api_index):,}**"
        )

    except Exception as e:

        st.error(
            f"❌ Ошибка чтения API-файла: {e}"
        )

        api_index = None


# ============================================================
# ПРЕДВАРИТЕЛЬНАЯ ОБРАБОТКА ЛИСТА ПОДБОРА
# ============================================================

label_index = {}

if picklist_file:

    with st.spinner(
        "📋 Анализирую лист подбора..."
    ):

        mappings = build_picklist_mapping(
            picklist_file
        )

        label_index = build_label_index(
            mappings
        )

    st.success(
        f"✅ На листе подбора найдено связок: "
        f"**{len(label_index):,}**"
    )

    if mappings:

        with st.expander(
            "🔎 Показать найденные связи"
        ):

            import pandas as pd

            mapping_df = pd.DataFrame(
                mappings
            )

            if not mapping_df.empty:

                mapping_df = mapping_df[
                    [
                        "label_key",
                        "order",
                        "page",
                        "method"
                    ]
                ]

                mapping_df.columns = [
                    "Ключ этикетки",
                    "Номер отправления",
                    "Страница",
                    "Метод"
                ]

                st.dataframe(
                    mapping_df,
                    use_container_width=True
                )


# ============================================================
# КНОПКА СОЗДАНИЯ
# ============================================================

st.divider()

ready = (
    labels_file is not None
    and api_file is not None
    and picklist_file is not None
    and api_index is not None
)

if not ready:

    st.info(
        "⬆️ Загрузите все три файла."
    )

else:

    if st.button(
        "🚀 СОЗДАТЬ ГОТОВЫЙ PDF",
        type="primary",
        use_container_width=True
    ):

        with st.spinner(
            "⏳ Обрабатываю этикетки..."
        ):

            try:

                (
                    output,
                    stats,
                    diagnostics
                ) = process_pdf(
                    labels_file,
                    api_index,
                    label_index
                )

                # ============================================
                # РЕЗУЛЬТАТ
                # ============================================

                st.success(
                    "✅ Готовый PDF сформирован!"
                )

                # ============================================
                # СТАТИСТИКА
                # ============================================

                col1, col2, col3, col4 = st.columns(4)

                with col1:
                    st.metric(
                        "Всего этикеток",
                        stats["total"]
                    )

                with col2:
                    st.metric(
                        "По номеру",
                        stats["direct"]
                    )

                with col3:
                    st.metric(
                        "Через лист подбора",
                        stats["picklist"]
                    )

                with col4:
                    st.metric(
                        "Не найдено",
                        stats["unresolved"]
                    )

                # ============================================
                # ДИАГНОСТИКА
                # ============================================

                import pandas as pd

                diagnostic_df = pd.DataFrame(
                    diagnostics
                )

                if not diagnostic_df.empty:

                    with st.expander(
                        "🔎 Подробный результат обработки"
                    ):

                        st.dataframe(
                            diagnostic_df,
                            use_container_width=True,
                            height=500
                        )

                # ============================================
                # СКАЧИВАНИЕ
                # ============================================

                st.download_button(
                    label="📥 СКАЧАТЬ ГОТОВЫЙ PDF",
                    data=output.getvalue(),
                    file_name=(
                        "OZON_FBS_ГОТОВЫЕ_ЭТИКЕТКИ.pdf"
                    ),
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True
                )

            except Exception as e:

                st.error(
                    "❌ Ошибка обработки:"
                )

                st.exception(
                    e
                )
