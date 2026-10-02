import streamlit as st
import re
import os
from io import BytesIO

from pypdf import PdfReader, PdfWriter
import pdfplumber

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import requests


# ============================================================
# НАСТРОЙКИ
# ============================================================

st.set_page_config(
    page_title="Умная склейка этикеток Ozon",
    page_icon="🖨️",
    layout="wide"
)

st.title("🖨️ Склейка: Этикетки + Лист подбора")

st.write(
    "Сопоставление этикеток Ozon с листом подбора "
    "по номеру отправления и коду этикетки."
)


# ============================================================
# ШРИФТ
# ============================================================

@st.cache_resource
def load_font():

    font_path = "Roboto_Full_Final.ttf"

    if not os.path.exists(font_path):

        url = (
            "https://cdnjs.cloudflare.com/ajax/libs/"
            "pdfmake/0.1.66/fonts/Roboto/Roboto-Regular.ttf"
        )

        r = requests.get(url, timeout=30)
        r.raise_for_status()

        with open(font_path, "wb") as f:
            f.write(r.content)

    try:
        pdfmetrics.getFont("OzonFont")
    except Exception:
        pdfmetrics.registerFont(
            TTFont("OzonFont", font_path)
        )

    return "OzonFont"


font_name = load_font()


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================

def normalize_text(value):

    if value is None:
        return ""

    value = str(value)

    value = (
        value
        .replace("\u00a0", " ")
        .replace("і", "i")
        .replace("І", "i")
    )

    return value


def normalize_order(order):

    if not order:
        return ""

    order = normalize_text(order)

    order = re.sub(
        r"\s+",
        "",
        order
    )

    return order.lower()


# ============================================================
# КОРОТКИЙ КОД ЭТИКЕТКИ
# ============================================================

def get_short_code(order):

    order = normalize_order(order)

    if not order:
        return ""

    # Например:
    # 78277691-0407-1 -> 7691

    if "-" in order:

        first_part = order.split("-")[0]

        digits = re.sub(
            r"\D",
            "",
            first_part
        )

        return digits[-4:]

    # Например:
    # 150103202537 -> 2537
    # ii50103202537 -> 2537

    digits = re.sub(
        r"\D",
        "",
        order
    )

    return digits[-4:]


# ============================================================
# ЦИФРОВОЙ КЛЮЧ
# ============================================================

def get_numeric_key(order):

    order = normalize_order(order)

    return re.sub(
        r"\D",
        "",
        order
    )


# ============================================================
# ПОИСК НОМЕРОВ ОТПРАВЛЕНИЯ
# ============================================================

ORDER_PATTERN = re.compile(
    r"""
    (
        \d{8,15}-\d{4}-\d+
        |
        [a-zA-Z]{0,4}\d{10,15}
    )
    """,
    re.IGNORECASE | re.VERBOSE
)


def find_orders(text):

    if not text:
        return []

    return ORDER_PATTERN.findall(
        normalize_text(text)
    )


# ============================================================
# ОЧИСТКА НОМЕРОВ
# ============================================================

def remove_order_numbers(
    text,
    orders
):

    if not text:
        return ""

    result = normalize_text(text)

    for order in orders:

        order_norm = normalize_order(order)

        if not order_norm:
            continue

        # Полный номер

        result = re.sub(
            re.escape(order_norm),
            " ",
            result,
            flags=re.IGNORECASE
        )

        # Исходное написание

        result = re.sub(
            re.escape(str(order)),
            " ",
            result,
            flags=re.IGNORECASE
        )

        # Последние 4 цифры
        #
        # ВАЖНО:
        # удаляем их только как отдельное значение.

        short_code = get_short_code(
            order
        )

        if short_code:

            result = re.sub(
                rf"(?<!\d){re.escape(short_code)}(?!\d)",
                " ",
                result
            )

    return result


# ============================================================
# ОЧИСТКА БЛОКА
# ============================================================

def clean_assembly_text(
    text,
    orders
):

    if not text:
        return ""

    result = normalize_text(text)

    result = remove_order_numbers(
        result,
        orders
    )

    # Заголовки

    headers = [

        r"Склад МСК ООО.*?",

        r"Склад:.*?",

        r"Служба доставки:.*?",

        r"Номер отправления",

        r"Номер с этикетки",

        r"Количество отправлений",

        r"Дата:",

        r"Фото",

        r"Товар",

        r"Артикул",

        r"Кол-во",

        r"Этикетка",

        r"Ozon",

        r"Проверьте список.*?отменять их\.",

        r"№"
    ]

    for pattern in headers:

        result = re.sub(
            pattern,
            " ",
            result,
            flags=re.IGNORECASE
        )

    result = result.replace(
        "|",
        " "
    )

    result = re.sub(
        r"[ \t]+",
        " ",
        result
    )

    return result.strip()


# ============================================================
# ПОИСК ДАННЫХ В ТАБЛИЧНОМ БЛОКЕ
#
# СТРУКТУРА:
#
# №
# Номер отправления
# Номер с этикетки
# Фото
# Товар
# Артикул
# Кол-во
# Этикетка
#
# 1
# 78277691-0407-1
# Антикоррозионное покрытие...
# MW1801
# 1
# 7691
# ============================================================


def extract_article_and_qty(
    text,
    forbidden_codes
):

    if not text:
        return "-", "1"

    text = normalize_text(text)

    # --------------------------------------------------------
    # Сначала удаляем номера
    # --------------------------------------------------------

    for code in forbidden_codes:

        if not code:
            continue

        text = re.sub(
            rf"(?<!\d){re.escape(code)}(?!\d)",
            " ",
            text
        )

    # --------------------------------------------------------
    # Строки
    # --------------------------------------------------------

    raw_lines = text.splitlines()

    lines = []

    for line in raw_lines:

        line = line.replace(
            "\u00a0",
            " "
        )

        line = re.sub(
            r"[ \t]+",
            " ",
            line
        ).strip()

        if line:
            lines.append(line)

    # --------------------------------------------------------
    # Убираем мусорные строки
    # --------------------------------------------------------

    filtered = []

    for line in lines:

        low = line.lower().strip()

        if low in (
            "№",
            "фото",
            "товар",
            "артикул",
            "кол-во",
            "этикетка",
            "номер отправления",
            "номер с этикетки"
        ):
            continue

        filtered.append(line)

    lines = filtered

    # --------------------------------------------------------
    # Единицы / мусор
    # --------------------------------------------------------

    units = {
        "мл",
        "шт",
        "шт.",
        "г",
        "кг",
        "л",
        "mm",
        "cm",
        "m",
        "ml",
        "kg",
        "g",
        "штук",
        "уп"
    }

    # ========================================================
    # 1. ИЩЕМ АРТИКУЛ
    # ========================================================

    article_candidates = []

    for line in lines:

        tokens = re.findall(
            r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_\-/.]*",
            line
        )

        for token in tokens:

            token = token.strip()

            if not token:
                continue

            if token in forbidden_codes:
                continue

            if token.isdigit():
                continue

            if len(token) < 2:
                continue

            if token.lower() in units:
                continue

            # Хороший артикул обычно содержит
            # буквы + цифры

            if (
                re.search(r"[A-Za-zА-Яа-я]", token)
                and re.search(r"\d", token)
            ):
                article_candidates.append(
                    token
                )

    # Предпочитаем последний подходящий
    article = "-"

    if article_candidates:

        article = article_candidates[-1]

    # ========================================================
    # 2. ИЩЕМ КОЛИЧЕСТВО
    #
    # КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ:
    #
    # Не ищем любую цифру в тексте.
    #
    # 7691 никогда не станет количеством.
    #
    # Ищем только короткое число 1-3 цифры,
    # стоящее отдельно.
    # ========================================================

    qty_candidates = []

    for line in lines:

        # Только строка, состоящая исключительно
        # из количества.

        m = re.fullmatch(
            r"(\d{1,3})",
            line.strip()
        )

        if m:

            value = m.group(1)

            # Никогда не берем запрещенный код

            if value in forbidden_codes:
                continue

            number = int(value)

            if 1 <= number <= 999:

                qty_candidates.append(
                    value
                )

    # Берем последнее отдельное число.
    #
    # В вашем PDF после очистки:
    #
    # MW1801
    # 1
    #
    # получится:
    #
    # article = MW1801
    # qty = 1

    if qty_candidates:

        qty = qty_candidates[-1]

    else:

        # Если количество не удалось надежно определить,
        # лучше поставить 1, чем вытащить мусор
        # из названия товара.

        qty = "1"

    # ========================================================
    # 3. ДОПОЛНИТЕЛЬНАЯ ЗАЩИТА
    # ========================================================

    if qty in forbidden_codes:

        qty = "1"

    # 4-значное значение никогда не количество

    if len(str(qty)) >= 4:

        qty = "1"

    return article, qty


# ============================================================
# ПОЛУЧЕНИЕ НАЗВАНИЯ
# ============================================================

def extract_product_name(
    text,
    article,
    qty,
    forbidden_codes
):

    if not text:
        return "Товар"

    result = normalize_text(text)

    # Убираем номер

    for code in forbidden_codes:

        if code:

            result = re.sub(
                rf"(?<!\d){re.escape(code)}(?!\d)",
                " ",
                result
            )

    # Убираем артикул

    if article and article != "-":

        result = re.sub(
            re.escape(article),
            " ",
            result,
            flags=re.IGNORECASE
        )

    # Убираем количество,
    # только если это отдельное значение

    if qty:

        result = re.sub(
            rf"(?<!\d){re.escape(str(qty))}(?!\d)",
            " ",
            result
        )

    # Заголовки

    headers = [

        r"Склад МСК ООО.*?",

        r"Склад:.*?",

        r"Служба доставки:.*?",

        r"Номер отправления",

        r"Номер с этикетки",

        r"Количество отправлений",

        r"Дата:",

        r"Фото",

        r"Товар",

        r"Артикул",

        r"Кол-во",

        r"Этикетка",

        r"Ozon",

        r"№"
    ]

    for pattern in headers:

        result = re.sub(
            pattern,
            " ",
            result,
            flags=re.IGNORECASE
        )

    result = result.replace(
        "|",
        " "
    )

    # Удаляем одиночные порядковые числа

    result = re.sub(
        r"(?m)^\s*\d+\s*$",
        " ",
        result
    )

    result = re.sub(
        r"\s+",
        " ",
        result
    ).strip()

    if not result:
        result = "Товар"

    # ========================================================
    # ВАЖНО:
    # оставляем только первые 20 символов
    # ========================================================

    if len(result) > 20:

        result = result[:20].rstrip()

    return result


# ============================================================
# ДОБАВЛЕНИЕ КЛЮЧЕЙ В MAP
# ============================================================

def add_mapping(
    data,
    order,
    item
):

    order_norm = normalize_order(
        order
    )

    if not order_norm:
        return

    # --------------------------------------------------------
    # Полный номер
    # --------------------------------------------------------

    data[
        order_norm
    ] = item

    # --------------------------------------------------------
    # Цифровой номер
    # --------------------------------------------------------

    num_key = get_numeric_key(
        order_norm
    )

    if num_key:

        data[
            num_key
        ] = item

    # --------------------------------------------------------
    # Последние 10 цифр
    # --------------------------------------------------------

    if len(num_key) >= 10:

        data[
            num_key[-10:]
        ] = item

    # --------------------------------------------------------
    # Код этикетки
    # --------------------------------------------------------

    short_code = get_short_code(
        order_norm
    )

    if short_code:

        # Не перезаписываем существующий ключ,
        # если он уже уникально найден.

        if short_code not in data:

            data[
                short_code
            ] = item


# ============================================================
# ПАРСИНГ ЛИСТА ПОДБОРА
#
# ГЛАВНОЕ:
# РАЗБИВАЕМ СТРОГО ПО ГРАФИЧЕСКИМ
# ГОРИЗОНТАЛЬНЫМ ЛИНИЯМ.
# ============================================================

def parse_assembly_list(
    pdf_file
):

    data = {}

    stats = {
        "pages": 0,
        "blocks": 0,
        "orders": 0,
        "matched_blocks": 0
    }

    with pdfplumber.open(
        pdf_file
    ) as pdf:

        stats["pages"] = len(
            pdf.pages
        )

        progress = st.progress(
            0
        )

        status_text = st.empty()

        for page_index, page in enumerate(
            pdf.pages
        ):

            status_text.text(
                f"📄 Лист подбора: "
                f"{page_index + 1}/"
                f"{len(pdf.pages)}"
            )

            # =================================================
            # ГОРИЗОНТАЛЬНЫЕ ЛИНИИ
            # =================================================

            horizontal_lines = []

            for line in page.lines:

                try:

                    width = float(
                        line.get(
                            "width",
                            0
                        )
                    )

                    height = abs(
                        float(
                            line.get(
                                "height",
                                0
                            )
                        )
                    )

                    # Горизонтальная линия
                    # должна быть длинной
                    # и практически без высоты.

                    if (
                        width > 30
                        and height <= 2
                    ):

                        horizontal_lines.append(
                            line
                        )

                except Exception:
                    continue

            horizontal_lines.sort(
                key=lambda x: float(
                    x.get(
                        "top",
                        0
                    )
                )
            )

            # =================================================
            # ГРАНИЦЫ БЛОКОВ
            # =================================================

            y_coords = [0]

            for line in horizontal_lines:

                y = float(
                    line.get(
                        "top",
                        0
                    )
                )

                y_coords.append(
                    y
                )

            y_coords.append(
                float(page.height)
            )

            # Удаляем дубли

            y_coords = sorted(
                set(
                    round(
                        y,
                        2
                    )
                    for y in y_coords
                )
            )

            # =================================================
            # ОБРАБАТЫВАЕМ КАЖДЫЙ БЛОК
            # =================================================

            for i in range(
                len(y_coords) - 1
            ):

                top = y_coords[i]
                bottom = y_coords[i + 1]

                # Слишком маленький блок
                # пропускаем

                if (
                    bottom - top < 15
                ):
                    continue

                stats["blocks"] += 1

                bbox = (
                    0,
                    top,
                    page.width,
                    bottom
                )

                try:

                    crop = page.within_bbox(
                        bbox
                    )

                    # layout=True оставляем,
                    # потому что таблица имеет
                    # колонную структуру.

                    text = crop.extract_text(
                        layout=True
                    )

                except Exception:

                    continue

                if not text:
                    continue

                # =================================================
                # ИЩЕМ НОМЕРА
                # =================================================

                orders_in_slice = find_orders(
                    text
                )

                if not orders_in_slice:
                    continue

                stats["orders"] += len(
                    orders_in_slice
                )

                # Уникальные номера

                unique_orders = []

                for order in orders_in_slice:

                    norm = normalize_order(
                        order
                    )

                    if norm not in unique_orders:

                        unique_orders.append(
                            norm
                        )

                orders_in_slice = unique_orders

                # =================================================
                # КОДЫ ЭТИКЕТОК
                # =================================================

                forbidden_codes = set()

                for order in orders_in_slice:

                    code = get_short_code(
                        order
                    )

                    if code:

                        forbidden_codes.add(
                            code
                        )

                # =================================================
                # ОЧИСТКА
                # =================================================

                text_clean = clean_assembly_text(
                    text,
                    orders_in_slice
                )

                # =================================================
                # АРТИКУЛ + КОЛИЧЕСТВО
                # =================================================

                article, qty = extract_article_and_qty(
                    text_clean,
                    forbidden_codes
                )

                # =================================================
                # НАЗВАНИЕ
                # =================================================

                name = extract_product_name(
                    text_clean,
                    article,
                    qty,
                    forbidden_codes
                )

                # =================================================
                # ITEM
                # =================================================

                item = {
                    "name": name,
                    "article": article,
                    "qty": qty
                }

                # =================================================
                # МАППИНГ КАЖДОГО НОМЕРА
                # =================================================

                for order in orders_in_slice:

                    add_mapping(
                        data,
                        order,
                        item
                    )

                    stats[
                        "matched_blocks"
                    ] += 1

            progress.progress(
                (page_index + 1)
                / len(pdf.pages)
            )

        status_text.text(
            "✅ Лист подбора обработан"
        )

    return data, stats


# ============================================================
# ПОИСК НА ЭТИКЕТКЕ
# ============================================================

def extract_order_from_label(
    text
):

    if not text:
        return None

    text = normalize_text(
        text
    )

    # --------------------------------------------------------
    # Убираем технические подчеркивания
    # --------------------------------------------------------

    text = re.sub(
        r"_\d+",
        "",
        text
    )

    # --------------------------------------------------------
    # Склеиваем:
    #
    # 78277691
    # -0407-1
    #
    # --------------------------------------------------------

    text_compact = re.sub(
        r"\s+",
        "",
        text
    )

    # --------------------------------------------------------
    # Стандартный номер
    # --------------------------------------------------------

    m = re.search(
        r"\d{8,15}-\d{4}-\d+",
        text_compact
    )

    if m:

        return m.group(
            0
        )

    # --------------------------------------------------------
    # ii + цифры
    # --------------------------------------------------------

    m = re.search(
        r"ii\d{8,20}",
        text_compact,
        re.IGNORECASE
    )

    if m:

        return m.group(
            0
        )

    # --------------------------------------------------------
    # Просто длинный номер
    # --------------------------------------------------------

    m = re.search(
        r"\d{10,15}",
        text_compact
    )

    if m:

        return m.group(
            0
        )

    return None


# ============================================================
# ПОИСК ИНФОРМАЦИИ
# ============================================================

def find_product_info(
    assembly_data,
    order
):

    if not order:

        return None

    order_norm = normalize_order(
        order
    )

    # ========================================================
    # 1. ПОЛНОЕ СОВПАДЕНИЕ
    # ========================================================

    info = assembly_data.get(
        order_norm
    )

    if info:

        return info

    # ========================================================
    # 2. ЦИФРОВОЙ КЛЮЧ
    # ========================================================

    numeric = get_numeric_key(
        order_norm
    )

    if numeric:

        info = assembly_data.get(
            numeric
        )

        if info:

            return info

    # ========================================================
    # 3. ПОСЛЕДНИЕ 10 ЦИФР
    # ========================================================

    if len(numeric) >= 10:

        info = assembly_data.get(
            numeric[-10:]
        )

        if info:

            return info

    # ========================================================
    # 4. КОД ЭТИКЕТКИ
    # ========================================================

    short_code = get_short_code(
        order_norm
    )

    if short_code:

        info = assembly_data.get(
            short_code
        )

        if info:

            return info

    return None


# ============================================================
# ИНФОРМАЦИОННАЯ СТРАНИЦА
# ============================================================

def create_info_label(
    width,
    height,
    order_number,
    product_info
):

    packet = BytesIO()

    c = canvas.Canvas(
        packet,
        pagesize=(
            width,
            height
        )
    )

    x_margin = 10

    # ========================================================
    # ЗАКАЗ
    # ========================================================

    c.setFont(
        font_name,
        10
    )

    c.drawString(
        x_margin,
        height - 18,
        f"Заказ: {order_number}"
    )

    c.line(
        x_margin,
        height - 20,
        width - x_margin,
        height - 20
    )

    # ========================================================
    # АРТИКУЛ
    # ========================================================

    c.setFont(
        font_name,
        12
    )

    article = str(
        product_info.get(
            "article",
            "-"
        )
    )

    if len(article) > 25:

        article = (
            article[:22]
            + "..."
        )

    c.drawString(
        x_margin,
        height - 35,
        f"Арт: {article}"
    )

    # ========================================================
    # НАЗВАНИЕ
    # ========================================================

    name = str(
        product_info.get(
            "name",
            "Товар не найден"
        )
    )

    # Дополнительная защита:
    # название максимум 20 символов.

    if len(name) > 20:

        name = name[:20].rstrip()

    top_limit = (
        height - 52
    )

    bottom_limit = 50

    available_h = (
        top_limit
        - bottom_limit
    )

    current_size = 10
    line_h = 12

    def get_lines(
        txt,
        chars
    ):

        words = txt.split()

        result = []

        current = ""

        for word in words:

            if (
                len(current)
                + len(word)
                + 1
                <= chars
            ):

                current += (
                    word
                    + " "
                )

            else:

                if current:

                    result.append(
                        current.strip()
                    )

                current = (
                    word
                    + " "
                )

        if current:

            result.append(
                current.strip()
            )

        return result

    lines = get_lines(
        name,
        32
    )

    while (
        len(lines)
        * line_h
        > available_h
        and current_size > 6
    ):

        current_size -= 0.5
        line_h -= 0.6

        lines = get_lines(
            name,
            int(
                32
                * (
                    10
                    / current_size
                )
            )
        )

    # ========================================================
    # НАЗВАНИЕ
    # ========================================================

    c.setFont(
        font_name,
        current_size
    )

    y_text = top_limit

    for line in lines:

        if y_text > bottom_limit:

            c.drawString(
                x_margin,
                y_text,
                line
            )

            y_text -= line_h

    # ========================================================
    # КОЛИЧЕСТВО
    # ========================================================

    c.setFont(
        font_name,
        24
    )

    qty = str(
        product_info.get(
            "qty",
            "1"
        )
    )

    # КРИТИЧЕСКАЯ ЗАЩИТА:
    # 4 цифры никогда не печатаем как количество.

    if len(qty) >= 4:

        qty = "1"

    c.drawString(
        x_margin,
        15,
        f"КОЛ-ВО: {qty}"
    )

    c.save()

    packet.seek(0)

    return PdfReader(
        packet
    ).pages[0]


# ============================================================
# ИНТЕРФЕЙС
# ============================================================

col1, col2 = st.columns(
    2
)

with col1:

    labels_file = st.file_uploader(
        "1️⃣ Этикетки (PDF)",
        type="pdf"
    )

with col2:

    assembly_file = st.file_uploader(
        "2️⃣ Лист подбора отправлений (PDF)",
        type="pdf"
    )


# ============================================================
# СКЛЕЙКА
# ============================================================

if (
    labels_file
    and assembly_file
):

    if st.button(
        "🚀 Склеить файлы",
        type="primary",
        use_container_width=True
    ):

        total_labels = 0
        success_count = 0

        error_orders = []

        with st.status(
            "Анализ и склейка...",
            expanded=True
        ) as status:

            # =================================================
            # ЛИСТ ПОДБОРА
            # =================================================

            st.write(
                "🔎 Читаю лист подбора..."
            )

            assembly_data, assembly_stats = (
                parse_assembly_list(
                    assembly_file
                )
            )

            st.write(
                f"📄 Страниц листа подбора: "
                f"{assembly_stats['pages']}"
            )

            st.write(
                f"📦 Найдено отправлений: "
                f"{assembly_stats['orders']}"
            )

            st.write(
                f"🔑 Индексов сопоставления: "
                f"{len(assembly_data)}"
            )

            # =================================================
            # ЭТИКЕТКИ
            # =================================================

            st.write(
                "🏷️ Читаю этикетки..."
            )

            reader = PdfReader(
                labels_file
            )

            writer = PdfWriter()

            total_labels = len(
                reader.pages
            )

            # =================================================
            # КАЖДАЯ ЭТИКЕТКА
            # =================================================

            for i in range(
                total_labels
            ):

                page = reader.pages[i]

                # Оригинальная этикетка

                writer.add_page(
                    page
                )

                try:

                    text = (
                        page.extract_text()
                        or ""
                    )

                except Exception:

                    text = ""

                # =================================================
                # ИЩЕМ НОМЕР
                # =================================================

                full_num = extract_order_from_label(
                    text
                )

                w = float(
                    page.mediabox.width
                )

                h = float(
                    page.mediabox.height
                )

                # =================================================
                # ЕСЛИ НОМЕР НАЙДЕН
                # =================================================

                if full_num:

                    info = find_product_info(
                        assembly_data,
                        full_num
                    )

                    display_num = (
                        full_num.upper()
                    )

                    if (
                        display_num.startswith(
                            "II"
                        )
                    ):

                        display_num = (
                            "ii"
                            + display_num[2:]
                        )

                    # =================================================
                    # НЕ НАЙДЕН
                    # =================================================

                    if not info:

                        info = {
                            "name": "Товар не найден",
                            "article": "-",
                            "qty": "1"
                        }

                        error_orders.append(
                            f"Страница {i + 1}: "
                            f"{display_num}"
                        )

                    else:

                        # =================================================
                        # ФИНАЛЬНАЯ ЗАЩИТА QTY
                        # =================================================

                        info = dict(
                            info
                        )

                        qty = str(
                            info.get(
                                "qty",
                                "1"
                            )
                        )

                        # 4 цифры = это НЕ количество

                        if len(qty) >= 4:

                            info["qty"] = "1"

                        # Запрещенный код

                        short_code = get_short_code(
                            full_num
                        )

                        if (
                            info["qty"]
                            == short_code
                        ):

                            info["qty"] = "1"

                        success_count += 1

                    # =================================================
                    # ИНФОРМАЦИОННАЯ СТРАНИЦА
                    # =================================================

                    writer.add_page(
                        create_info_label(
                            w,
                            h,
                            display_num,
                            info
                        )
                    )

                # =================================================
                # НОМЕР НЕ РАСПОЗНАН
                # =================================================

                else:

                    info = {
                        "name": "Номер не распознан",
                        "article": "-",
                        "qty": "-"
                    }

                    writer.add_page(
                        create_info_label(
                            w,
                            h,
                            "???",
                            info
                        )
                    )

                    error_orders.append(
                        f"Страница {i + 1}: "
                        "номер не распознан"
                    )

            status.update(
                label="✅ Обработка завершена!",
                state="complete"
            )

        # ====================================================
        # ДИАГНОСТИКА
        # ====================================================

        st.divider()

        col_m1, col_m2, col_m3 = st.columns(
            3
        )

        col_m1.metric(
            "Всего этикеток",
            total_labels
        )

        col_m2.metric(
            "Успешно привязано",
            success_count
        )

        col_m3.metric(
            "Ошибок",
            total_labels - success_count
        )

        # ====================================================
        # РЕЗУЛЬТАТ
        # ====================================================

        if (
            success_count
            == total_labels
        ):

            st.success(
                "✅ Все этикетки успешно "
                "сопоставлены с листом подбора."
            )

        else:

            st.error(
                f"⚠️ Не найдено: "
                f"{total_labels - success_count}"
            )

            if error_orders:

                st.write(
                    "Проблемные этикетки:"
                )

                for err in error_orders[:100]:

                    st.markdown(
                        f"- **{err}**"
                    )

                if len(error_orders) > 100:

                    st.caption(
                        f"... и ещё "
                        f"{len(error_orders) - 100}"
                    )

        # ====================================================
        # СОХРАНЕНИЕ
        # ====================================================

        output = BytesIO()

        writer.write(
            output
        )

        output.seek(0)

        st.download_button(
            "📥 Скачать PDF для печати",
            output,
            "Ready_Labels.pdf",
            "application/pdf",
            type="primary",
            use_container_width=True
        )
