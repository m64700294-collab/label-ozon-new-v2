import streamlit as st
import os
import re
import io
import base64
import time
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

APP_TITLE = "Ozon FBS — обработка этикеток"

# Размер информационной страницы.
# A4 оставляем для текущего режима.
INFO_PAGE_SIZE = A4

# Шрифты.
# DejaVu здесь НЕ используется.
FONT_CANDIDATES = [
    "Roboto-Regular.ttf",
    "Roboto_Full_Final.ttf",
    "OzonFont_Fix.ttf",
]

PRIMARY_FONT_NAME = "OzonRoboto"
FALLBACK_FONT_NAME = "OzonRobotoFallback"


# ============================================================
# БАЗОВЫЕ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def normalize_text(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\xa0", " ")
    value = value.replace("–", "-")
    value = value.replace("—", "-")
    value = value.replace("−", "-")

    return re.sub(r"\s+", " ", value).strip()


def normalize_order(value):
    """
    Нормализация номера отправления.

    Например:

    87180955-0554-25
    87180955 0554 25
    87180955–0554–25

    -> 87180955-0554-25
    """

    value = normalize_text(value)

    if not value:
        return ""

    value = value.replace(" ", "")
    value = value.replace("–", "-")
    value = value.replace("—", "-")

    parts = re.findall(r"\d+", value)

    if len(parts) >= 3:
        return "-".join(parts[:3])

    return value


def normalize_article(value):
    value = normalize_text(value)
    return value


def safe_int(value, default=0):
    try:
        if pd.isna(value):
            return default

        value = str(value).strip()
        if not value:
            return default

        value = value.replace(",", ".")
        return int(float(value))

    except Exception:
        return default


def clean_filename(name):
    name = re.sub(r"[^\w\-. ]+", "_", name or "")
    return name[:150]


# ============================================================
# ШРИФТ
# ============================================================

@st.cache_resource
def load_fonts():
    """
    Регистрируем Roboto.
    DejaVu намеренно не используется.

    Приоритет:
    1. Roboto-Regular.ttf
    2. Roboto_Full_Final.ttf
    3. OzonFont_Fix.ttf
    """

    registered = []

    for filename in FONT_CANDIDATES:
        path = os.path.join(os.getcwd(), filename)

        if not os.path.exists(path):
            continue

        try:
            font_name = PRIMARY_FONT_NAME if not registered else FALLBACK_FONT_NAME

            pdfmetrics.registerFont(
                TTFont(font_name, path)
            )

            registered.append(
                {
                    "name": font_name,
                    "path": path,
                }
            )

        except Exception:
            continue

    if not registered:
        return None

    return registered[0]["name"]


FONT_NAME = load_fonts()


# ============================================================
# ПОИСК КОЛОНОК В XLSX / CSV
# ============================================================

def find_column(df, variants):
    """
    Ищет колонку по нескольким вариантам.
    Сначала точное совпадение, затем contains.
    """

    if df is None or df.empty:
        return None

    columns = list(df.columns)

    normalized_columns = {}

    for col in columns:
        normalized_columns[col] = normalize_text(col).lower()

    # Точное совпадение
    for variant in variants:
        v = normalize_text(variant).lower()

        for original, normalized in normalized_columns.items():
            if normalized == v:
                return original

    # Частичное совпадение
    for variant in variants:
        v = normalize_text(variant).lower()

        for original, normalized in normalized_columns.items():
            if v in normalized:
                return original

    return None


# ============================================================
# ЗАГРУЗКА API XLSX / CSV
# ============================================================

def read_api_file(uploaded_file):
    """
    Читает XLSX / XLS / CSV.

    Возвращает DataFrame.
    """

    if uploaded_file is None:
        return None

    filename = uploaded_file.name.lower()

    try:
        if filename.endswith(".xlsx"):
            return pd.read_excel(uploaded_file)

        if filename.endswith(".xls"):
            return pd.read_excel(uploaded_file)

        if filename.endswith(".csv"):
            raw = uploaded_file.getvalue()

            for encoding in ["utf-8-sig", "utf-8", "cp1251"]:
                try:
                    text = raw.decode(encoding)

                    return pd.read_csv(
                        io.StringIO(text),
                        sep=None,
                        engine="python"
                    )

                except Exception:
                    continue

        raise ValueError(
            "Поддерживаются только XLSX, XLS и CSV."
        )

    except Exception as e:
        raise RuntimeError(
            f"Не удалось прочитать файл API: {e}"
        )


# ============================================================
# ИНДЕКС API ДАННЫХ
# ============================================================

def build_api_index(df):
    """
    Создаёт индекс:

    Номер отправления
        ->
    {
        order,
        article,
        name,
        qty
    }

    Один номер отправления может встречаться
    несколько раз, например при нескольких товарах.

    Поэтому дополнительно строится индекс товаров.
    """

    if df is None or df.empty:
        return {
            "orders": {},
            "rows": [],
            "columns": {},
        }

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

    columns = {
        "order": order_col,
        "article": article_col,
        "name": name_col,
        "qty": qty_col,
    }

    orders = defaultdict(list)
    rows = []

    for _, row in df.iterrows():

        raw_order = (
            row.get(order_col, "")
            if order_col is not None
            else ""
        )

        order = normalize_order(raw_order)

        if not order:
            continue

        article = (
            normalize_article(row.get(article_col, ""))
            if article_col is not None
            else "-"
        )

        name = (
            normalize_text(row.get(name_col, ""))
            if name_col is not None
            else "НЕ НАЙДЕНО"
        )

        qty = (
            safe_int(row.get(qty_col, 1), 1)
            if qty_col is not None
            else 1
        )

        item = {
            "order": order,
            "article": article or "-",
            "name": name or "НЕ НАЙДЕНО",
            "qty": qty,
        }

        orders[order].append(item)
        rows.append(item)

    return {
        "orders": dict(orders),
        "rows": rows,
        "columns": columns,
    }


# ============================================================
# ДАННЫЕ ОТПРАВЛЕНИЯ ИЗ API
# ============================================================

def get_order_data(api_index, order):
    """
    Возвращает агрегированные данные по отправлению.

    Если в отправлении несколько товаров:
      Арт: A / B
      Название: товар 1 / товар 2
      КОЛ-ВО: 2

    """

    order = normalize_order(order)

    if not order:
        return None

    items = api_index.get("orders", {}).get(order)

    if not items:
        return None

    articles = []
    names = []
    total_qty = 0

    for item in items:

        article = item.get("article", "-")
        name = item.get("name", "НЕ НАЙДЕНО")
        qty = safe_int(item.get("qty", 1), 1)

        if article and article not in articles:
            articles.append(article)

        if name and name not in names:
            names.append(name)

        total_qty += qty

    return {
        "order": order,
        "article": " / ".join(articles) if articles else "-",
        "name": " / ".join(names) if names else "НЕ НАЙДЕНО",
        "qty": total_qty if total_qty > 0 else 1,
    }


# ============================================================
# ПОИСК НОМЕРОВ ОТПРАВЛЕНИЯ В PDF
# ============================================================

ORDER_PATTERN = re.compile(
    r"\b\d{6,15}\s*[-–—]?\s*\d{2,8}\s*[-–—]?\s*\d{1,8}\b"
)


def find_order_candidates(text):
    """
    ВАЖНО:

    Мы больше не считаем любой похожий номер
    автоматически номером отправления.

    Функция только возвращает кандидатов.

    Проверка принадлежности отправления выполняется
    через API index.
    """

    if not text:
        return []

    text = text.replace("\xa0", " ")

    found = []

    for match in ORDER_PATTERN.finditer(text):
        candidate = normalize_order(match.group(0))

        if candidate and candidate not in found:
            found.append(candidate)

    return found


# ============================================================
# НОВАЯ ЭТИКЕТКА — ВНУТРЕННИЙ КОД
# ============================================================

def find_internal_label_codes(text):
    """
    Вспомогательная диагностика для новых наклеек.

    Например:

        II5010320 2549
        II50103202549

    Мы МОЖЕМ увидеть этот код.

    Но:

        !!! НЕ используем последние 4 цифры
        !!! как основной способ определения заказа.

    Это принципиальное изменение архитектуры.
    """

    if not text:
        return []

    text = text.replace("\xa0", " ")

    variants = []

    patterns = [
        r"\b(?:II|Il|I1|1I|ll)\s*\d{6,10}\s*\d{4}\b",
        r"\b(?:II|Il|I1|1I|ll)\s*\d{6,10}\d{4}\b",
        r"\bii\d{6,10}\s*\d{4}\b",
        r"\bii\d{10,14}\b",
    ]

    for pattern in patterns:
        for match in re.finditer(
            pattern,
            text,
            flags=re.IGNORECASE
        ):
            value = normalize_text(match.group(0))

            if value and value not in variants:
                variants.append(value)

    return variants


# ============================================================
# PDF: ИЗВЛЕЧЕНИЕ ТЕКСТА
# ============================================================

def extract_pdf_pages(pdf_bytes):
    """
    Возвращает:

    [
        {
            "page": 1,
            "text": "...",
        }
    ]
    """

    pages = []

    with pdfplumber.open(
        io.BytesIO(pdf_bytes)
    ) as pdf:

        for page_number, page in enumerate(
            pdf.pages,
            start=1
        ):

            try:
                text = page.extract_text(
                    x_tolerance=2,
                    y_tolerance=3
                ) or ""

            except Exception:
                text = ""

            pages.append(
                {
                    "page": page_number,
                    "text": text,
                }
            )

    return pages


# ============================================================
# РЕЗЕРВНЫЙ АНАЛИЗ ЛИСТА ПОДБОРА
# ============================================================

def build_picklist_index(picklist_bytes):
    """
    Лист подбора теперь НЕ является основным источником.

    Он нужен как резерв:

        внутренний код -> номер отправления

    если Ozon/PDF всё-таки позволяет получить такую связь
    через текст листа.

    """

    if not picklist_bytes:
        return {
            "key_to_orders": {},
            "order_to_keys": {},
        }

    key_to_orders = defaultdict(set)
    order_to_keys = defaultdict(set)

    try:
        with pdfplumber.open(
            io.BytesIO(picklist_bytes)
        ) as pdf:

            for page in pdf.pages:

                words = page.extract_words(
                    x_tolerance=2,
                    y_tolerance=3,
                    keep_blank_chars=False
                )

                if not words:
                    continue

                # ------------------------------------------------
                # Группируем слова в строки
                # ------------------------------------------------

                lines = []

                for word in sorted(
                    words,
                    key=lambda x: (
                        round(float(x.get("top", 0)), 1),
                        float(x.get("x0", 0))
                    )
                ):

                    top = float(word.get("top", 0))

                    target = None

                    for line in lines:
                        if abs(line["top"] - top) <= 4:
                            target = line
                            break

                    if target is None:
                        target = {
                            "top": top,
                            "words": []
                        }

                        lines.append(target)

                    target["words"].append(word)

                # ------------------------------------------------
                # Ищем номер отправления + 4 цифры
                # ------------------------------------------------

                for line in lines:

                    line_words = sorted(
                        line["words"],
                        key=lambda x: float(x.get("x0", 0))
                    )

                    line_text = " ".join(
                        normalize_text(
                            x.get("text", "")
                        )
                        for x in line_words
                    )

                    orders = find_order_candidates(
                        line_text
                    )

                    if not orders:
                        continue

                    keys = set()

                    for word in line_words:

                        token = normalize_text(
                            word.get("text", "")
                        )

                        if re.fullmatch(
                            r"\d{4}",
                            token
                        ):
                            keys.add(token)

                    for order in orders:
                        for key in keys:
                            key_to_orders[key].add(order)
                            order_to_keys[order].add(key)

    except Exception:
        return {
            "key_to_orders": {},
            "order_to_keys": {},
        }

    return {
        "key_to_orders": dict(key_to_orders),
        "order_to_keys": dict(order_to_keys),
    }


# ============================================================
# РЕЗЕРВНОЕ СОПОСТАВЛЕНИЕ НОВОГО КОДА
# ============================================================

def try_picklist_fallback(
    internal_codes,
    picklist_index,
    api_index
):
    """
    Очень осторожный fallback.

    Мы НЕ берём последние 4 цифры напрямую.

    Только если внутренний код содержит 4-значную часть,
    и лист подбора однозначно связывает её с заказом,
    который существует в API.

    """

    if not internal_codes:
        return None, None

    key_to_orders = picklist_index.get(
        "key_to_orders",
        {}
    )

    if not key_to_orders:
        return None, None

    for code in internal_codes:

        # Получаем только потенциальные 4 цифры
        # для резервной проверки.
        keys = re.findall(
            r"(?<!\d)\d{4}(?!\d)",
            code
        )

        for key in keys:

            candidates = set(
                key_to_orders.get(key, set())
            )

            # Оставляем только те,
            # которые реально есть в API.
            valid = []

            for order in candidates:
                if order in api_index.get("orders", {}):
                    valid.append(order)

            if len(valid) == 1:
                return (
                    normalize_order(valid[0]),
                    key
                )

    return None, None


# ============================================================
# АРХИТЕКТУРА ПРОВАЙДЕРА ЭТИКЕТОК
# ============================================================

class LabelProvider:
    """
    Базовый интерфейс.

    Сейчас используем ExistingPdfLabelProvider.

    Позже сюда можно подключить Ozon API:

        OzonApiLabelProvider

    Тогда остальная программа вообще не должна знать,
    откуда пришёл PDF.
    """

    def get_pdf(self):
        raise NotImplementedError


class ExistingPdfLabelProvider(LabelProvider):
    """
    Текущий режим:

    пользователь загружает уже готовый PDF.
    """

    def __init__(self, pdf_bytes):
        self.pdf_bytes = pdf_bytes

    def get_pdf(self):
        return self.pdf_bytes


class OzonApiLabelProvider(LabelProvider):
    """
    Заготовка для нового API.

    Сейчас НЕ вызывается автоматически.

    В актуальном API найден метод:

        POST /v2/posting/fbs/package-label

    Он принимает posting_number.

    Но этот класс специально отделён от текущего режима,
    чтобы изменение API не ломало обработку уже
    полученного PDF.

    """

    BASE_URL = "https://api-seller.ozon.ru"

    def __init__(
        self,
        client_id,
        api_key,
        posting_numbers
    ):
        self.client_id = client_id
        self.api_key = api_key
        self.posting_numbers = posting_numbers

    def get_pdf(self):
        """
        Пока намеренно не выполняем HTTP-запрос здесь.

        Подключение requests можно добавить отдельным этапом,
        когда в приложении будут заданы Client-Id / Api-Key
        и окончательно подтверждён рабочий production flow
        новых scanit-этикеток.
        """

        raise NotImplementedError(
            "Получение PDF через Ozon API будет подключено "
            "отдельным провайдером."
        )


# ============================================================
# СОПОСТАВЛЕНИЕ СТРАНИЦ PDF
# ============================================================

def resolve_page(
    page_number,
    text,
    api_index,
    picklist_index,
    known_postings=None
):
    """
    Главная функция распознавания.

    Приоритет:

    1. Явный номер отправления в PDF,
       который существует в API.

    2. Если PDF не содержит номера,
       но приложение получило известный список posting_number,
       используем номер по позиции страницы.

    3. Резервно проверяем внутренний код через лист подбора.

    4. Если ничего не получилось:
       НЕРАСПОЗНАНО.

    """

    text = text or ""

    api_orders = api_index.get(
        "orders",
        {}
    )

    # --------------------------------------------------------
    # 1. Ищем обычные номера
    # --------------------------------------------------------

    candidates = find_order_candidates(text)

    valid_api_candidates = []

    for candidate in candidates:

        normalized = normalize_order(candidate)

        if normalized in api_orders:
            valid_api_candidates.append(normalized)

    # Если нашли ровно один реальный номер
    if len(valid_api_candidates) == 1:

        order = valid_api_candidates[0]

        return {
            "page": page_number,
            "type": "Прямой номер",
            "key": "",
            "order": order,
            "api_found": True,
            "data": get_order_data(
                api_index,
                order
            ),
            "raw_text": text,
        }

    # Если несколько, выбираем только однозначный
    if len(valid_api_candidates) > 1:

        counts = Counter(
            valid_api_candidates
        )

        most_common = counts.most_common()

        if (
            len(most_common) == 1
            or (
                len(most_common) > 1
                and most_common[0][1] > most_common[1][1]
            )
        ):

            order = most_common[0][0]

            return {
                "page": page_number,
                "type": "Прямой номер",
                "key": "",
                "order": order,
                "api_found": True,
                "data": get_order_data(
                    api_index,
                    order
                ),
                "raw_text": text,
            }

    # --------------------------------------------------------
    # 2. НОВАЯ ЭТИКЕТКА БЕЗ НОМЕРА
    #
    # Если у нас есть список отправлений,
    # который в будущем придёт прямо из API,
    # можем использовать позиционное соответствие.
    # --------------------------------------------------------

    if known_postings:

        if page_number <= len(known_postings):

            order = normalize_order(
                known_postings[page_number - 1]
            )

            if order in api_orders:

                return {
                    "page": page_number,
                    "type": "API-позиция",
                    "key": "",
                    "order": order,
                    "api_found": True,
                    "data": get_order_data(
                        api_index,
                        order
                    ),
                    "raw_text": text,
                }

    # --------------------------------------------------------
    # 3. Внутренний код новой наклейки
    # --------------------------------------------------------

    internal_codes = find_internal_label_codes(
        text
    )

    # --------------------------------------------------------
    # 4. Осторожный fallback через лист подбора
    # --------------------------------------------------------

    order, key = try_picklist_fallback(
        internal_codes,
        picklist_index,
        api_index
    )

    if order:

        return {
            "page": page_number,
            "type": "Резерв: лист подбора",
            "key": key or "",
            "order": order,
            "api_found": True,
            "data": get_order_data(
                api_index,
                order
            ),
            "raw_text": text,
        }

    # --------------------------------------------------------
    # 5. Нераспознанная
    # --------------------------------------------------------

    return {
        "page": page_number,
        "type": (
            "Новая этикетка без номера"
            if internal_codes
            else "Не распознано"
        ),
        "key": (
            ", ".join(internal_codes)
            if internal_codes
            else ""
        ),
        "order": "",
        "api_found": False,
        "data": None,
        "raw_text": text,
    }


# ============================================================
# СОЗДАНИЕ ИНФОРМАЦИОННОЙ СТРАНИЦЫ
# ============================================================

def draw_wrapped_text(
    c,
    text,
    x,
    y,
    max_width,
    font_name,
    font_size,
    leading
):
    """
    Рисует текст с переносом по ширине.
    """

    text = normalize_text(text)

    if not text:
        return y

    words = text.split()

    lines = []
    current = ""

    for word in words:

        test = (
            word
            if not current
            else current + " " + word
        )

        try:
            width = pdfmetrics.stringWidth(
                test,
                font_name,
                font_size
            )
        except Exception:
            width = len(test) * font_size * 0.5

        if width <= max_width:
            current = test
        else:

            if current:
                lines.append(current)

            current = word

    if current:
        lines.append(current)

    for line in lines:

        c.drawString(
            x,
            y,
            line
        )

        y -= leading

    return y


def create_info_page(info):
    """
    Создаёт вторую страницу.

    Формат:

        ИНФОРМАЦИЯ ПО ОТПРАВЛЕНИЮ

        Заказ: ...
        Арт: ...
        Название: ...
        КОЛ-ВО: ...

    """

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=INFO_PAGE_SIZE
    )

    width, height = INFO_PAGE_SIZE

    font = FONT_NAME

    # Если шрифт не загрузился,
    # используем стандартный Helvetica.
    # Для кириллицы он может не отображаться,
    # поэтому в нормальной установке нужен Roboto.
    if not font:
        font = "Helvetica"

    left = 45
    right = 45
    max_width = width - left - right

    # --------------------------------------------------------
    # Заголовок
    # --------------------------------------------------------

    c.setFont(
        font,
        22
    )

    c.drawString(
        left,
        height - 70,
        "ИНФОРМАЦИЯ ПО ОТПРАВЛЕНИЮ"
    )

    # --------------------------------------------------------
    # Разделительная линия
    # --------------------------------------------------------

    c.setLineWidth(1)

    c.line(
        left,
        height - 90,
        width - right,
        height - 90
    )

    # --------------------------------------------------------
    # Данные
    # --------------------------------------------------------

    order = info.get(
        "order",
        ""
    ) or "НЕ РАСПОЗНАН"

    data = info.get(
        "data"
    )

    if data:

        article = data.get(
            "article",
            "-"
        )

        name = data.get(
            "name",
            "НЕ НАЙДЕНО"
        )

        qty = data.get(
            "qty",
            1
        )

    else:

        article = "-"
        name = "НЕ НАЙДЕНО"
        qty = "?"

    y = height - 145

    # Заказ
    c.setFont(
        font,
        17
    )

    c.drawString(
        left,
        y,
        f"Заказ: {order}"
    )

    y -= 45

    # Артикул
    c.setFont(
        font,
        16
    )

    c.drawString(
        left,
        y,
        f"Арт: {article}"
    )

    y -= 45

    # Название
    c.setFont(
        font,
        16
    )

    c.drawString(
        left,
        y,
        "Название:"
    )

    y -= 28

    y = draw_wrapped_text(
        c=c,
        text=name,
        x=left,
        y=y,
        max_width=max_width,
        font_name=font,
        font_size=16,
        leading=24
    )

    # --------------------------------------------------------
    # Количество
    # --------------------------------------------------------

    y -= 65

    c.setFont(
        font,
        30
    )

    c.drawString(
        left,
        y,
        f"КОЛ-ВО: {qty}"
    )

    # --------------------------------------------------------
    # Диагностический тип
    # --------------------------------------------------------

    y -= 45

    c.setFont(
        font,
        10
    )

    c.drawString(
        left,
        y,
        f"Источник сопоставления: {info.get('type', '')}"
    )

    # --------------------------------------------------------
    # Внутренний ключ — только мелко,
    # если он действительно был найден.
    # --------------------------------------------------------

    key = info.get(
        "key",
        ""
    )

    if key:

        y -= 18

        c.drawString(
            left,
            y,
            f"Внутренний код: {key}"
        )

    c.showPage()
    c.save()

    buffer.seek(0)

    return buffer.getvalue()


# ============================================================
# ДОБАВЛЕНИЕ ИНФОРМАЦИОННЫХ СТРАНИЦ
# ============================================================

def build_result_pdf(
    original_pdf_bytes,
    results
):
    """
    Для каждой исходной страницы:

        оригинальная страница
        +
        информационная страница

    """

    source_reader = PdfReader(
        io.BytesIO(original_pdf_bytes)
    )

    writer = PdfWriter()

    for index, page in enumerate(
        source_reader.pages
    ):

        writer.add_page(page)

        if index < len(results):
            info_pdf = create_info_page(
                results[index]
            )

            info_reader = PdfReader(
                io.BytesIO(info_pdf)
            )

            writer.add_page(
                info_reader.pages[0]
            )

    output = io.BytesIO()

    writer.write(output)

    output.seek(0)

    return output.getvalue()


# ============================================================
# ДИАГНОСТИКА
# ============================================================

def diagnostics_dataframe(results):
    rows = []

    for result in results:

        data = result.get("data") or {}

        rows.append(
            {
                "Страница": result.get(
                    "page",
                    ""
                ),

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

                "API найден": (
                    "Да"
                    if result.get(
                        "api_found"
                    )
                    else "Нет"
                ),

                "Артикул": data.get(
                    "article",
                    "-"
                ),

                "Количество": data.get(
                    "qty",
                    "?"
                ),
            }
        )

    return pd.DataFrame(rows)


# ============================================================
# ПОПЫТКА ПОЛУЧИТЬ СПИСОК ОТПРАВЛЕНИЙ ИЗ API XLSX
# ============================================================

def get_known_postings_from_api(api_index):
    """
    Возвращает уникальный список posting_number
    в порядке первого появления в XLSX.

    ВАЖНО:

    Это не идеальный порядок для будущего API PDF.

    Поэтому использовать его для позиционного
    сопоставления безопасно только если PDF был
    сформирован именно из этого же списка и в том же порядке.

    """

    result = []

    for item in api_index.get(
        "rows",
        []
    ):

        order = normalize_order(
            item.get("order", "")
        )

        if order and order not in result:
            result.append(order)

    return result


# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="📦",
    layout="wide"
)

st.title("📦 Ozon FBS — обработка этикеток")

st.caption(
    "Текущий режим: готовый PDF → API XLSX → "
    "информационная страница. "
    "Архитектура подготовлена под новый источник этикеток Ozon API."
)


# ============================================================
# СТАТУС ШРИФТА
# ============================================================

with st.expander(
    "🔤 Диагностика шрифта",
    expanded=False
):

    if FONT_NAME:

        st.success(
            f"Используется шрифт: {FONT_NAME}"
        )

    else:

        st.error(
            "Roboto не найден. "
            "Положите Roboto-Regular.ttf, "
            "Roboto_Full_Final.ttf или OzonFont_Fix.ttf "
            "рядом с app.py."
        )

    st.write(
        "Ожидаемые файлы:"
    )

    for filename in FONT_CANDIDATES:

        exists = os.path.exists(
            os.path.join(
                os.getcwd(),
                filename
            )
        )

        st.write(
            f"{'✅' if exists else '❌'} {filename}"
        )


# ============================================================
# ЗАГРУЗКА ФАЙЛОВ
# ============================================================

st.subheader("1. Файлы")

col1, col2 = st.columns(2)

with col1:

    labels_file = st.file_uploader(
        "📄 PDF этикеток Ozon",
        type=["pdf"],
        key="labels_pdf"
    )

with col2:

    api_file = st.file_uploader(
        "📊 XLSX / CSV с данными отправлений",
        type=["xlsx", "xls", "csv"],
        key="api_file"
    )

picklist_file = st.file_uploader(
    "📋 Лист подбора — необязательно",
    type=["pdf"],
    key="picklist_pdf"
)


# ============================================================
# БУДУЩИЙ РЕЖИМ API
# ============================================================

with st.expander(
    "🔌 Новый режим Ozon API — подготовлено",
    expanded=False
):

    st.info(
        "Сейчас приложение работает с уже скачанным PDF. "
        "Ниже оставлена архитектура для подключения "
        "получения этикеток через новый Seller API."
    )

    st.code(
        """Ozon API
    ↓
список posting_number
    ↓
формирование PDF этикеток
    ↓
полученный PDF
    ↓
этот же обработчик
    ↓
Артикул / Название / КОЛ-ВО""",
        language="text"
    )

    st.caption(
        "Полученный PDF должен поступать в тот же "
        "LabelProvider, поэтому распознавание и "
        "создание информационных страниц не зависит "
        "от способа получения PDF."
    )


# ============================================================
# ПРОВЕРКА
# ============================================================

if labels_file is None:

    st.warning(
        "Загрузите PDF этикеток."
    )

    st.stop()


if api_file is None:

    st.warning(
        "Загрузите XLSX/CSV с данными отправлений."
    )

    st.stop()


# ============================================================
# ЧТЕНИЕ API
# ============================================================

try:

    with st.spinner(
        "Читаю данные отправлений..."
    ):

        api_df = read_api_file(
            api_file
        )

        api_index = build_api_index(
            api_df
        )

except Exception as e:

    st.error(
        str(e)
    )

    st.stop()


# ============================================================
# ИНФОРМАЦИЯ ОБ API
# ============================================================

st.subheader("2. Данные отправлений")

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        "Строк API",
        len(
            api_index.get(
                "rows",
                []
            )
        )
    )

with col2:
    st.metric(
        "Отправлений",
        len(
            api_index.get(
                "orders",
                {}
            )
        )
    )

with col3:
    st.write(
        "**Номер:**"
    )

    st.write(
        api_index.get(
            "columns",
            {}
        ).get(
            "order"
        )
        or "не найден"
    )

with col4:
    st.write(
        "**Количество:**"
    )

    st.write(
        api_index.get(
            "columns",
            {}
        ).get(
            "qty"
        )
        or "не найдено"
    )


if not api_index.get("orders"):

    st.error(
        "В XLSX/CSV не найдено ни одного номера отправления."
    )

    st.write(
        "Найденные колонки:"
    )

    st.write(
        api_index.get(
            "columns",
            {}
        )
    )

    st.stop()


# ============================================================
# ЛИСТ ПОДБОРА
# ============================================================

picklist_index = {
    "key_to_orders": {},
    "order_to_keys": {},
}

if picklist_file is not None:

    with st.spinner(
        "Анализирую лист подбора..."
    ):

        picklist_bytes = (
            picklist_file.getvalue()
        )

        picklist_index = build_picklist_index(
            picklist_bytes
        )

    key_count = len(
        picklist_index.get(
            "key_to_orders",
            {}
        )
    )

    st.caption(
        f"Лист подбора: найдено внутренних ключей — {key_count}"
    )


# ============================================================
# ПОЛУЧАЕМ PDF ЧЕРЕЗ ПРОВАЙДЕР
# ============================================================

original_pdf_bytes = labels_file.getvalue()

provider = ExistingPdfLabelProvider(
    original_pdf_bytes
)

pdf_bytes = provider.get_pdf()


# ============================================================
# ИЗВЛЕЧЕНИЕ СТРАНИЦ
# ============================================================

with st.spinner(
    "Анализирую страницы PDF..."
):

    pdf_pages = extract_pdf_pages(
        pdf_bytes
    )


st.subheader("3. Распознавание")

st.write(
    f"Страниц в исходном PDF: **{len(pdf_pages)}**"
)


# ============================================================
# ИЗВЕСТНЫЕ ОТПРАВЛЕНИЯ
# ============================================================

known_postings = []

use_position_mapping = False

st.markdown(
    "### Сопоставление новых этикеток"
)

position_mode = st.checkbox(
    "Использовать позиционное сопоставление для этикеток без номера",
    value=False,
    help=(
        "Включайте только если этот PDF сформирован "
        "из точно этого же списка отправлений "
        "и порядок отправлений совпадает."
    )
)

if position_mode:

    known_postings = get_known_postings_from_api(
        api_index
    )

    use_position_mapping = True

    st.warning(
        f"Позиционный режим включён. "
        f"Доступно отправлений: {len(known_postings)}. "
        f"Это безопасно только при совпадении порядка."
    )


# ============================================================
# ОБРАБОТКА
# ============================================================

if st.button(
    "🚀 Обработать PDF",
    type="primary",
    use_container_width=True
):

    results = []

    progress = st.progress(
        0
    )

    status = st.empty()

    for i, page_info in enumerate(
        pdf_pages
    ):

        page_number = page_info["page"]
        text = page_info["text"]

        status.write(
            f"Страница {page_number} / {len(pdf_pages)}"
        )

        result = resolve_page(
            page_number=page_number,
            text=text,
            api_index=api_index,
            picklist_index=picklist_index,
            known_postings=(
                known_postings
                if use_position_mapping
                else None
            )
        )

        results.append(
            result
        )

        progress.progress(
            int(
                ((i + 1) / len(pdf_pages)) * 100
            )
        )

    status.empty()

    st.session_state["results"] = results
    st.session_state["original_pdf_bytes"] = pdf_bytes


# ============================================================
# РЕЗУЛЬТАТЫ
# ============================================================

if "results" in st.session_state:

    results = st.session_state[
        "results"
    ]

    original_pdf_bytes = st.session_state[
        "original_pdf_bytes"
    ]

    st.subheader("4. Результат распознавания")

    total = len(results)

    found = sum(
        1
        for x in results
        if x.get("api_found")
    )

    unresolved = total - found

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Всего страниц",
            total
        )

    with col2:
        st.metric(
            "Распознано",
            found
        )

    with col3:
        st.metric(
            "Не распознано",
            unresolved
        )

    # --------------------------------------------------------
    # Таблица
    # --------------------------------------------------------

    df_diag = diagnostics_dataframe(
        results
    )

    st.dataframe(
        df_diag,
        use_container_width=True,
        hide_index=True
    )

    # --------------------------------------------------------
    # Нераспознанные
    # --------------------------------------------------------

    unresolved_results = [
        x
        for x in results
        if not x.get("api_found")
    ]

    if unresolved_results:

        st.warning(
            f"Нераспознано страниц: "
            f"{len(unresolved_results)}"
        )

        with st.expander(
            "🔎 Подробная диагностика нераспознанных страниц",
            expanded=False
        ):

            for item in unresolved_results:

                st.markdown(
                    f"### Страница {item.get('page')}"
                )

                st.write(
                    f"**Тип:** {item.get('type')}"
                )

                st.write(
                    f"**Ключ:** {item.get('key') or '-'}"
                )

                st.write(
                    f"**Заказ:** "
                    f"{item.get('order') or '-'}"
                )

                raw_text = item.get(
                    "raw_text",
                    ""
                )

                st.text_area(
                    "Извлечённый текст",
                    raw_text[:5000],
                    height=160,
                    key=f"raw_{item.get('page')}"
                )

                st.divider()

    # --------------------------------------------------------
    # Создание итогового PDF
    # --------------------------------------------------------

    st.subheader(
        "5. Формирование итогового PDF"
    )

    try:

        with st.spinner(
            "Создаю итоговый PDF..."
        ):

            output_pdf = build_result_pdf(
                original_pdf_bytes,
                results
            )

        filename = (
            "Ozon_FBS_готовый_"
            f"{len(results)}_отправлений.pdf"
        )

        st.download_button(
            label="📥 Скачать итоговый PDF",
            data=output_pdf,
            file_name=filename,
            mime="application/pdf",
            type="primary",
            use_container_width=True
        )

        st.success(
            "Готово. На каждую исходную страницу "
            "добавлена отдельная информационная страница."
        )

    except Exception as e:

        st.error(
            f"Ошибка формирования PDF: {e}"
        )


# ============================================================
# ПРЕДПРОСМОТР API
# ============================================================

with st.expander(
    "📊 Первые строки API",
    expanded=False
):

    st.dataframe(
        api_df.head(50),
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# ТЕХНИЧЕСКАЯ ИНФОРМАЦИЯ
# ============================================================

with st.expander(
    "⚙️ Техническая информация",
    expanded=False
):

    st.write(
        "### Текущая архитектура"
    )

    st.code(
        """ExistingPdfLabelProvider
        ↓
     PDF
        ↓
extract_pdf_pages()
        ↓
resolve_page()
        ↓
 ┌──────────────────────────────┐
 │ 1. Номер отправления в PDF   │
 │ 2. API-позиция (опционально) │
 │ 3. Лист подбора — fallback   │
 │ 4. НЕ РАСПОЗНАНО             │
 └──────────────────────────────┘
        ↓
build_result_pdf()
        ↓
Оригинал + Информация""",
        language="text"
    )

    st.write(
        "### Что специально НЕ используется"
    )

    st.write(
        "❌ автоматическое принятие последних 4 цифр "
        "как номера заказа"
    )

    st.write(
        "❌ автоматическое преобразование "
        "`II50103202549` в заказ"
    )

    st.write(
        "❌ угадывание отправления по похожему номеру"
    )

    st.write(
        "### Подготовлено для нового API"
    )

    st.write(
        "Отдельный `LabelProvider` позволяет заменить "
        "источник PDF без переписывания обработки "
        "и создания информационных страниц."
    )
