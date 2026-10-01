```python
import io
import re
import os
import requests
import streamlit as st

import pdfplumber

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader


# ============================================================
# НАСТРОЙКИ
# ============================================================

APP_TITLE = "🖨️ Склейка: Этикетки + Лист подбора"

FONT_NAME = "OzonFont"
FONT_FILE = "Roboto_Full_Final.ttf"

# URL шрифта Roboto, если локального файла нет
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

st.title(APP_TITLE)

st.caption(
    "Загрузите PDF с этикетками и PDF с листом подбора. "
    "К каждой этикетке будет добавлен лист с товаром, артикулом и количеством."
)


# ============================================================
# ШРИФТ
# ============================================================

@st.cache_resource
def load_font():
    """
    Загружает Roboto для корректной работы с кириллицей.
    """

    # Если шрифт уже зарегистрирован — просто возвращаем имя.
    try:
        pdfmetrics.getFont(FONT_NAME)
        return FONT_NAME
    except Exception:
        pass

    # Ищем локальный файл
    local_candidates = [
        FONT_FILE,
        os.path.join(os.getcwd(), FONT_FILE),
        os.path.join(os.path.dirname(__file__), FONT_FILE)
        if "__file__" in globals()
        else ""
    ]

    font_path = None

    for candidate in local_candidates:
        if candidate and os.path.exists(candidate):
            font_path = candidate
            break

    # Если локального файла нет — скачиваем
    if not font_path:
        try:
            response = requests.get(
                FONT_URL,
                timeout=30
            )

            response.raise_for_status()

            font_path = FONT_FILE

            with open(font_path, "wb") as f:
                f.write(response.content)

        except Exception as e:
            st.error(
                "Не удалось загрузить шрифт Roboto. "
                f"Ошибка: {e}"
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
# РАБОТА С НОМЕРОМ ОТПРАВЛЕНИЯ
# ============================================================

ORDER_PATTERN = re.compile(
    r"""
    (
        \d{8,15}-\d{4}-\d+
        |
        [A-Za-zА-Яа-я]{0,4}\d{8,20}
    )
    """,
    re.IGNORECASE | re.VERBOSE
)


def normalize_order(value):
    """
    Нормализует номер отправления.
    """

    if not value:
        return ""

    value = str(value).strip()

    value = value.replace("\xa0", " ")

    value = re.sub(
        r"\s+",
        "",
        value
    )

    return value.upper()


def get_short_code(order):
    """
    Для:
        78277691-0407-1

    возвращает:
        7691

    То есть последние 4 цифры первой части.
    """

    order = normalize_order(order)

    if not order:
        return ""

    parts = order.split("-")

    if parts:
        first = re.sub(
            r"\D",
            "",
            parts[0]
        )

        if len(first) >= 4:
            return first[-4:]

    digits = re.sub(
        r"\D",
        "",
        order
    )

    if len(digits) >= 4:
        return digits[-4:]

    return digits


def get_numeric_key(order):
    """
    Только цифры из номера отправления.
    """

    if not order:
        return ""

    return re.sub(
        r"\D",
        "",
        str(order)
    )


def get_last10_key(order):
    """
    Последние 10 цифр номера.
    """

    numeric = get_numeric_key(order)

    if len(numeric) >= 10:
        return numeric[-10:]

    return numeric


def find_orders(text):
    """
    Находит номера отправлений в тексте блока.
    """

    if not text:
        return []

    matches = ORDER_PATTERN.findall(text)

    result = []

    for item in matches:
        normalized = normalize_order(item)

        if normalized and normalized not in result:
            result.append(normalized)

    return result


# ============================================================
# НОРМАЛИЗАЦИЯ ТЕКСТА
# ============================================================

def normalize_spaces(text):
    """
    Убирает лишние пробелы и переносы.
    """

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
        r"\s+",
        " ",
        text
    )

    return text.strip()


def normalize_field_names(text):
    """
    Приводит разные варианты написания полей
    к единому виду.
    """

    if not text:
        return ""

    text = text.replace(
        "\xa0",
        " "
    )

    # Номер отправления
    text = re.sub(
        r"Номер\s+отправления",
        "Номер отправления",
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

    # Фото Товар
    text = re.sub(
        r"Фото\s+Товар",
        "Товар",
        text,
        flags=re.IGNORECASE
    )

    # Фото + перенос + Товар
    text = re.sub(
        r"Фото\s+.*?\s+Товар",
        "Товар",
        text,
        flags=re.IGNORECASE
    )

    # Кол-во / Кол во / Кол-во
    text = re.sub(
        r"Кол\s*[-–—]?\s*во",
        "Кол-во",
        text,
        flags=re.IGNORECASE
    )

    # Артикул
    text = re.sub(
        r"\bАртикул\b",
        "Артикул",
        text,
        flags=re.IGNORECASE
    )

    # Этикетка
    text = re.sub(
        r"\bЭтикетка\b",
        "Этикетка",
        text,
        flags=re.IGNORECASE
    )

    return text


# ============================================================
# УДАЛЕНИЕ НОМЕРОВ ОТПРАВЛЕНИЙ
# ============================================================

def remove_orders(text, orders):
    """
    Удаляет номера отправлений из текста,
    чтобы они не попали случайно в товар/артикул.
    """

    if not text:
        return ""

    result = text

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
# ИЗВЛЕЧЕНИЕ МЕЖДУ ДВУМЯ ПОЛЯМИ
# ============================================================

def extract_between(
    text,
    start_pattern,
    end_pattern
):
    """
    Берёт содержимое между двумя маркерами.

    Например:

        Товар
        Антикоррозионное покрытие
        Артикул

    даст:

        Антикоррозионное покрытие
    """

    if not text:
        return ""

    pattern = (
        start_pattern
        + r"\s*(.*?)\s*"
        + end_pattern
    )

    match = re.search(
        pattern,
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    if not match:
        return ""

    result = match.group(1)

    result = normalize_spaces(result)

    return result.strip(
        " \t\r\n-–—:"
    )


# ============================================================
# РАЗБОР ТОВАРА
# ============================================================

def parse_product_block(text, orders):
    """
    Основной разбор блока листа подбора.

    Структура:

        Номер отправления
        78277691-0407-1

        Фото Товар
        Антикоррозионное покрытие для авто MW Service

        Артикул
        MW1801

        Кол-во
        1

        Этикетка
        7691

    Возвращает:

        {
            "name": "...",
            "article": "...",
            "qty": 1
        }
    """

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

    clean = text.replace(
        "\xa0",
        " "
    )

    clean = normalize_field_names(
        clean
    )

    # Сначала пробуем удалить номера отправлений
    clean_without_orders = remove_orders(
        clean,
        orders
    )

    # Очень важно:
    # переводим всё в одну строку.
    flat = normalize_spaces(
        clean_without_orders
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

    # Иногда в PDF перед "Товар" остаётся слово Фото.
    if not name:
        name = extract_between(
            flat,
            r"\bФото\s+Товар\b",
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
    # --------------------------------------------------------

    qty_block = extract_between(
        flat,
        r"\bКол-во\b",
        r"\bЭтикетка\b"
    )

    qty = 1

    if qty_block:

        # Только число, находящееся между Кол-во и Этикетка
        qty_match = re.search(
            r"(?<!\d)(\d{1,3})(?!\d)",
            qty_block
        )

        if qty_match:

            try:

                parsed_qty = int(
                    qty_match.group(1)
                )

                if parsed_qty > 0:
                    qty = parsed_qty

            except Exception:
                qty = 1

    # --------------------------------------------------------
    # ОЧИСТКА ТОВАРА
    # --------------------------------------------------------

    if name:

        name = normalize_spaces(
            name
        )

        # Если вдруг внутри остались служебные слова
        name = re.sub(
            r"^\s*(Фото\s+)?Товар\s*",
            "",
            name,
            flags=re.IGNORECASE
        )

        name = name.strip(
            " -–—:"
        )

    # --------------------------------------------------------
    # ОЧИСТКА АРТИКУЛА
    # --------------------------------------------------------

    if article:

        article = normalize_spaces(
            article
        )

        article = article.strip(
            " -–—:"
        )

        # Иногда PDF может вернуть служебное слово
        if article.lower() in {
            "кол-во",
            "этикетка",
            "товар"
        }:
            article = ""

    # --------------------------------------------------------
    # ЗАЩИТНЫЙ FALLBACK ДЛЯ АРТИКУЛА
    # --------------------------------------------------------

    if not article:

        # Ищем значение непосредственно после Артикул
        article_match = re.search(
            r"\bАртикул\b\s+"
            r"([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/-]{0,50})"
            r"\s+\bКол-во\b",
            flat,
            flags=re.IGNORECASE
        )

        if article_match:

            article = article_match.group(
                1
            ).strip()

    # --------------------------------------------------------
    # FALLBACK ДЛЯ ТОВАРА
    # --------------------------------------------------------

    if not name:

        # Ищем любой текст между Товар и Артикул
        match = re.search(
            r"\bТовар\b\s+"
            r"(.+?)"
            r"\s+\bАртикул\b",
            flat,
            flags=re.IGNORECASE
        )

        if match:

            name = normalize_spaces(
                match.group(1)
            ).strip(
                " -–—:"
            )

    # --------------------------------------------------------
    # Финальные значения
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
# ПОЛУЧЕНИЕ ГОРИЗОНТАЛЬНЫХ БЛОКОВ
# ============================================================

def get_horizontal_boundaries(page):
    """
    Получает горизонтальные линии PDF.

    Они используются для разделения товаров
    на отдельные блоки.
    """

    lines = []

    try:
        page_lines = page.lines or []
    except Exception:
        page_lines = []

    for line in page_lines:

        try:

            x0 = float(line.get("x0", 0))
            x1 = float(line.get("x1", 0))
            y0 = float(line.get("y0", 0))
            y1 = float(line.get("y1", 0))

            width = abs(x1 - x0)
            height = abs(y1 - y0)

            # Горизонтальная линия
            if width >= 30 and height <= 3:
                lines.append(
                    (y0, x0, x1)
                )

        except Exception:
            continue

    # Сортируем по Y
    lines.sort(
        key=lambda x: x[0]
    )

    return lines


# ============================================================
# ПАРСИНГ ЛИСТА ПОДБОРА
# ============================================================

def parse_assembly_list(pdf_file):
    """
    Читает PDF листа подбора.

    Возвращает словарь:

        {
            "7691": {
                "order": "78277691-0407-1",
                "name": "...",
                "article": "MW1801",
                "qty": 1
            }
        }
    """

    items = {}

    try:
        pdf_file.seek(0)
    except Exception:
        pass

    try:

        pdf_bytes = pdf_file.read()

        if not pdf_bytes:
            st.error(
                "PDF листа подбора пустой."
            )
            return items

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

            debug_blocks = []

            for page_number, page in enumerate(
                pdf.pages,
                start=1
            ):

                try:

                    lines = get_horizontal_boundaries(
                        page
                    )

                    # ------------------------------------------------
                    # Если горизонтальных линий нет
                    # ------------------------------------------------

                    if len(lines) < 2:

                        try:
                            full_text = (
                                page.extract_text(
                                    layout=False
                                )
                                or ""
                            )
                        except Exception:
                            full_text = ""

                        orders = find_orders(
                            full_text
                        )

                        if orders:

                            data = parse_product_block(
                                full_text,
                                orders
                            )

                            for order in orders:

                                short = get_short_code(
                                    order
                                )

                                numeric = get_numeric_key(
                                    order
                                )

                                last10 = get_last10_key(
                                    order
                                )

                                item = {
                                    "order": order,
                                    "name": data["name"],
                                    "article": data["article"],
                                    "qty": data["qty"]
                                }

                                if short:
                                    items[
                                        short
                                    ] = item

                                if numeric:
                                    items[
                                        "NUM_" + numeric
                                    ] = item

                                if last10:
                                    items[
                                        "LAST10_" + last10
                                    ] = item

                        progress.progress(
                            page_number / total_pages,
                            text=(
                                f"Страница "
                                f"{page_number}/{total_pages}"
                            )
                        )

                        continue

                    # ------------------------------------------------
                    # Формируем границы блоков
                    # ------------------------------------------------

                    y_values = [
                        line[0]
                        for line in lines
                    ]

                    # Убираем почти одинаковые линии
                    unique_y = []

                    for y in y_values:

                        if not unique_y:
                            unique_y.append(y)
                            continue

                        if abs(
                            y - unique_y[-1]
                        ) > 2:
                            unique_y.append(y)

                    # ------------------------------------------------
                    # Если линий всё равно мало
                    # ------------------------------------------------

                    if len(unique_y) < 2:

                        try:
                            full_text = (
                                page.extract_text(
                                    layout=False
                                )
                                or ""
                            )
                        except Exception:
                            full_text = ""

                        orders = find_orders(
                            full_text
                        )

                        if orders:

                            data = parse_product_block(
                                full_text,
                                orders
                            )

                            for order in orders:

                                item = {
                                    "order": order,
                                    "name": data["name"],
                                    "article": data["article"],
                                    "qty": data["qty"]
                                }

                                short = get_short_code(
                                    order
                                )

                                numeric = get_numeric_key(
                                    order
                                )

                                last10 = get_last10_key(
                                    order
                                )

                                if short:
                                    items[short] = item

                                if numeric:
                                    items[
                                        "NUM_" + numeric
                                    ] = item

                                if last10:
                                    items[
                                        "LAST10_" + last10
                                    ] = item

                        continue

                    # ------------------------------------------------
                    # Каждый участок между линиями = товар
                    # ------------------------------------------------

                    for i in range(
                        len(unique_y) - 1
                    ):

                        top = unique_y[i]
                        bottom = unique_y[i + 1]

                        # Не обрабатываем слишком маленькие блоки
                        if abs(
                            bottom - top
                        ) < 20:
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

                        # ------------------------------------------------
                        # Получаем текст ДВУМЯ способами.
                        #
                        # layout=False — для надёжного парсинга.
                        # layout=True — иногда лучше сохраняет
                        # визуальную структуру.
                        # ------------------------------------------------

                        text_normal = ""

                        try:
                            text_normal = (
                                cropped.extract_text(
                                    layout=False
                                )
                                or ""
                            )
                        except Exception:
                            pass

                        text_layout = ""

                        try:
                            text_layout = (
                                cropped.extract_text(
                                    layout=True
                                )
                                or ""
                            )
                        except Exception:
                            pass

                        # Для поиска используем normal.
                        # Если он пустой — layout.
                        text = (
                            text_normal
                            if text_normal.strip()
                            else text_layout
                        )

                        if not text.strip():
                            continue

                        # ------------------------------------------------
                        # Ищем номер отправления
                        # ------------------------------------------------

                        orders = find_orders(
                            text
                        )

                        if not orders:

                            # Иногда normal extraction
                            # теряет номер, а layout его сохраняет.
                            if text_layout:

                                orders = find_orders(
                                    text_layout
                                )

                                if orders:
                                    text = text_layout

                        if not orders:
                            continue

                        # ------------------------------------------------
                        # Парсим товар
                        # ------------------------------------------------

                        data = parse_product_block(
                            text,
                            orders
                        )

                        # ------------------------------------------------
                        # Создаём запись
                        # ------------------------------------------------

                        for order in orders:

                            item = {
                                "order": order,
                                "name": data["name"],
                                "article": data["article"],
                                "qty": data["qty"]
                            }

                            # -----------------------------
                            # Короткий код
                            # -----------------------------

                            short = get_short_code(
                                order
                            )

                            if short:

                                # Если код уже существует,
                                # не перезаписываем нормальный
                                # товар пустым.
                                old = items.get(
                                    short
                                )

                                if (
                                    old is None
                                    or (
                                        old.get("article") == "-"
                                        and data["article"] != "-"
                                    )
                                ):
                                    items[short] = item

                            # -----------------------------
                            # Полный numeric key
                            # -----------------------------

                            numeric = get_numeric_key(
                                order
                            )

                            if numeric:

                                items[
                                    "NUM_" + numeric
                                ] = item

                            # -----------------------------
                            # Последние 10 цифр
                            # -----------------------------

                            last10 = get_last10_key(
                                order
                            )

                            if last10:

                                items[
                                    "LAST10_" + last10
                                ] = item

                            # -----------------------------
                            # Debug
                            # -----------------------------

                            debug_blocks.append({
                                "page": page_number,
                                "order": order,
                                "short": short,
                                "name": data["name"],
                                "article": data["article"],
                                "qty": data["qty"]
                            })

                except Exception as e:

                    st.warning(
                        f"Ошибка на странице "
                        f"{page_number}: {e}"
                    )

                progress.progress(
                    page_number / total_pages,
                    text=(
                        f"Страница "
                        f"{page_number}/{total_pages}"
                    )
                )

            progress.empty()

    except Exception as e:

        st.error(
            "Не удалось обработать PDF листа подбора."
        )

        st.exception(e)

        return {}

    return items


# ============================================================
# ПОИСК ДАННЫХ ДЛЯ ЭТИКЕТКИ
# ============================================================

def find_item_for_order(
    order,
    items
):
    """
    Ищет товар по номеру отправления.

    Порядок:
        1. короткий код
        2. полный numeric
        3. последние 10 цифр
    """

    if not order:
        return None

    order = normalize_order(
        order
    )

    # 1. Short code
    short = get_short_code(
        order
    )

    if short:

        item = items.get(
            short
        )

        if item:
            return item

    # 2. Полный номер
    numeric = get_numeric_key(
        order
    )

    if numeric:

        item = items.get(
            "NUM_" + numeric
        )

        if item:
            return item

    # 3. Последние 10 цифр
    last10 = get_last10_key(
        order
    )

    if last10:

        item = items.get(
            "LAST10_" + last10
        )

        if item:
            return item

    return None


# ============================================================
# ИЗВЛЕЧЕНИЕ НОМЕРА С ЭТИКЕТКИ
# ============================================================

def extract_order_from_label(
    page
):
    """
    Извлекает номер отправления из страницы этикетки.
    """

    text = ""

    try:
        text = page.extract_text() or ""
    except Exception:
        pass

    if not text:
        try:
            text = page.extract_text(
                layout=True
            ) or ""
        except Exception:
            text = ""

    if not text:
        return ""

    orders = find_orders(
        text
    )

    if not orders:
        return ""

    # Предпочитаем длинный номер с дефисами
    hyphen_orders = [
        x for x in orders
        if "-" in x
    ]

    if hyphen_orders:
        return hyphen_orders[0]

    return orders[0]


# ============================================================
# ПЕРЕНОС ТЕКСТА
# ============================================================

def wrap_text(
    text,
    max_chars
):
    """
    Разбивает длинный текст на строки.
    """

    if not text:
        return [""]

    words = str(text).split()

    lines = []
    current = ""

    for word in words:

        if not current:

            current = word

        elif len(current) + 1 + len(word) <= max_chars:

            current += " " + word

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
# СОЗДАНИЕ ИНФОРМАЦИОННОЙ СТРАНИЦЫ
# ============================================================

def create_info_label(
    order,
    article,
    name,
    qty
):
    """
    Создаёт PDF-страницу A4
    с информацией по товару.
    """

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    width, height = A4

    # --------------------------------------------------------
    # Заголовок
    # --------------------------------------------------------

    c.setFont(
        FONT,
        24
    )

    c.drawString(
        50,
        height - 70,
        "ЛИСТ ПОДБОРА"
    )

    # --------------------------------------------------------
    # Номер отправления
    # --------------------------------------------------------

    c.setFont(
        FONT,
        15
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
        str(order or "-")
    )

    # --------------------------------------------------------
    # Артикул
    # --------------------------------------------------------

    c.setFont(
        FONT,
        15
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
        str(article or "-")
    )

    # --------------------------------------------------------
    # Количество
    # --------------------------------------------------------

    c.setFont(
        FONT,
        15
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
        str(qty or 1)
    )

    # --------------------------------------------------------
    # Товар
    # --------------------------------------------------------

    c.setFont(
        FONT,
        15
    )

    c.drawString(
        50,
        height - 390,
        "Товар:"
    )

    # Размер шрифта
    name_font_size = 20

    name_lines = wrap_text(
        name or "Товар",
        45
    )

    # Если название очень длинное —
    # уменьшаем шрифт.
    if len(name_lines) > 5:
        name_font_size = 16

    elif len(name_lines) > 3:
        name_font_size = 18

    c.setFont(
        FONT,
        name_font_size
    )

    y = height - 430

    for line in name_lines[:8]:

        c.drawString(
            50,
            y,
            line
        )

        y -= (
            name_font_size + 8
        )

    # --------------------------------------------------------
    # Рамка
    # --------------------------------------------------------

    c.setLineWidth(
        1.5
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
# ЧТЕНИЕ ЭТИКЕТОК
# ============================================================

def process_labels(
    labels_file,
    items
):
    """
    Добавляет после каждой страницы этикетки
    информационную страницу.
    """

    try:
        labels_file.seek(0)

        labels_bytes = labels_file.read()

        reader = PdfReader(
            io.BytesIO(labels_bytes)
        )

        writer = PdfWriter()

        total = len(
            reader.pages
        )

        progress = st.progress(
            0,
            text="Обрабатываем этикетки..."
        )

        found_count = 0
        not_found_count = 0

        not_found_orders = []

        for index, page in enumerate(
            reader.pages
        ):

            # ------------------------------------------------
            # Добавляем оригинальную этикетку
            # ------------------------------------------------

            writer.add_page(
                page
            )

            # ------------------------------------------------
            # Ищем номер отправления
            # ------------------------------------------------

            order = extract_order_from_label(
                page
            )

            # ------------------------------------------------
            # Ищем товар
            # ------------------------------------------------

            item = find_item_for_order(
                order,
                items
            )

            if item:

                found_count += 1

                info_pdf = create_info_label(
                    order=order,
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

                not_found_count += 1

                if order:
                    not_found_orders.append(
                        order
                    )

                info_pdf = create_info_label(
                    order=order or "Не найден",
                    article="-",
                    name="Товар не найден в листе подбора",
                    qty=1
                )

            # ------------------------------------------------
            # Добавляем информационную страницу
            # ------------------------------------------------

            info_reader = PdfReader(
                info_pdf
            )

            writer.add_page(
                info_reader.pages[0]
            )

            progress.progress(
                (index + 1) / total,
                text=(
                    f"Этикетка "
                    f"{index + 1}/{total}"
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
            found_count,
            not_found_count,
            not_found_orders
        )

    except Exception as e:

        st.error(
            "Ошибка при обработке этикеток."
        )

        st.exception(e)

        return (
            None,
            0,
            0,
            []
        )


# ============================================================
# ИНТЕРФЕЙС
# ============================================================

st.divider()

labels_file = st.file_uploader(
    "1️⃣ PDF с этикетками Ozon",
    type=["pdf"],
    key="labels_pdf"
)

assembly_file = st.file_uploader(
    "2️⃣ PDF с листом подбора Ozon",
    type=["pdf"],
    key="assembly_pdf"
)


# ============================================================
# КНОПКА
# ============================================================

if st.button(
    "🚀 Сформировать готовый PDF",
    type="primary",
    use_container_width=True
):

    if not labels_file:

        st.error(
            "Сначала загрузите PDF с этикетками."
        )

        st.stop()

    if not assembly_file:

        st.error(
            "Сначала загрузите PDF с листом подбора."
        )

        st.stop()

    # --------------------------------------------------------
    # Читаем лист подбора
    # --------------------------------------------------------

    with st.spinner(
        "🔎 Читаем лист подбора..."
    ):

        items = parse_assembly_list(
            assembly_file
        )

    if not items:

        st.error(
            "Не удалось найти товары в листе подбора."
        )

        st.warning(
            "Проверьте, что это PDF именно с листом подбора Ozon."
        )

        st.stop()

    # --------------------------------------------------------
    # Убираем технические ключи для статистики
    # --------------------------------------------------------

    unique_orders = set()

    for key, item in items.items():

        if key.startswith(
            ("NUM_", "LAST10_")
        ):
            continue

        order = item.get(
            "order"
        )

        if order:
            unique_orders.add(
                order
            )

    st.success(
        f"Найдено отправлений в листе подбора: "
        f"{len(unique_orders)}"
    )

    # --------------------------------------------------------
    # Показываем небольшую диагностику
    # --------------------------------------------------------

    with st.expander(
        "🔎 Проверить найденные товары"
    ):

        shown = set()

        for key, item in items.items():

            if key.startswith(
                ("NUM_", "LAST10_")
            ):
                continue

            order = item.get(
                "order",
                ""
            )

            if order in shown:
                continue

            shown.add(
                order
            )

            st.write(
                {
                    "Отправление": order,
                    "Артикул": item.get(
                        "article",
                        "-"
                    ),
                    "Товар": item.get(
                        "name",
                        "Товар"
                    ),
                    "Количество": item.get(
                        "qty",
                        1
                    ),
                    "Код": get_short_code(
                        order
                    )
                }
            )

    # --------------------------------------------------------
    # Формируем результат
    # --------------------------------------------------------

    with st.spinner(
        "🖨️ Формируем готовые этикетки..."
    ):

        result = process_labels(
            labels_file,
            items
        )

    output = result[0]

    if output is None:
        st.stop()

    found_count = result[1]
    not_found_count = result[2]
    not_found_orders = result[3]

    # --------------------------------------------------------
    # Статистика
    # --------------------------------------------------------

    st.divider()

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "Этикеток",
            found_count + not_found_count
        )

    with col2:
        st.metric(
            "Найдено",
            found_count
        )

    with col3:
        st.metric(
            "Не найдено",
            not_found_count
        )

    # --------------------------------------------------------
    # Ненайденные отправления
    # --------------------------------------------------------

    if not_found_orders:

        with st.expander(
            "⚠️ Отправления, для которых не найден товар"
        ):

            for order in not_found_orders:

                st.code(
                    order
                )

    # --------------------------------------------------------
    # Скачать
    # --------------------------------------------------------

    st.success(
        "✅ PDF успешно сформирован!"
    )

    st.download_button(
        label="📥 Скачать Ready_Labels.pdf",
        data=output.getvalue(),
        file_name="Ready_Labels.pdf",
        mime="application/pdf",
        use_container_width=True
    )
```
