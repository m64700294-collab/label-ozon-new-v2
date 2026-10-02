import streamlit as st
import re
import os
import csv
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import requests


# ============================================================
# НАСТРОЙКИ STREAMLIT
# ============================================================

st.set_page_config(
    page_title="Ozon — Этикетки + Лист подбора",
    page_icon="🖨️",
    layout="wide"
)

st.title("🖨️ Ozon — Этикетки + Лист подбора")

st.write(
    "Артикул и количество берутся из выгрузки Ozon API. "
    "PDF используется только для определения номера отправления."
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

        try:

            r = requests.get(
                url,
                timeout=30
            )

            r.raise_for_status()

            with open(font_path, "wb") as f:
                f.write(r.content)

        except Exception as e:

            st.error(
                f"Не удалось загрузить шрифт: {e}"
            )

            raise

    try:

        pdfmetrics.registerFont(
            TTFont(
                "OzonFont",
                font_path
            )
        )

    except Exception:
        pass

    return "OzonFont"


font_name = load_font()


# ============================================================
# НОРМАЛИЗАЦИЯ НОМЕРА ОТПРАВЛЕНИЯ
# ============================================================

def normalize_order(order):

    if order is None:
        return ""

    value = str(order).strip()

    if not value:
        return ""

    # Убираем невидимые символы
    value = (
        value
        .replace("\u200b", "")
        .replace("\xa0", " ")
    )

    # Приводим разные виды тире к обычному
    value = (
        value
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )

    # Убираем пробелы вокруг дефисов
    value = re.sub(
        r"\s*-\s*",
        "-",
        value
    )

    # В Ozon номер обычно цифровой
    value = value.lower()

    return value


# ============================================================
# НОРМАЛИЗАЦИЯ НОМЕРА ДЛЯ НАДЕЖНОГО СРАВНЕНИЯ
# ============================================================

def order_key(order):

    """
    Создает несколько вариантов ключа.

    Основной:
        34965873-0195-1

    Дополнительный:
        3496587301951
    """

    value = normalize_order(order)

    if not value:
        return ""

    return value


def numeric_order_key(order):

    value = normalize_order(order)

    return re.sub(
        r"\D",
        "",
        value
    )


# ============================================================
# ПОИСК НОМЕРА ОТПРАВЛЕНИЯ В ТЕКСТЕ PDF
# ============================================================

ORDER_PATTERN = re.compile(
    r"\d{6,15}\s*-\s*\d{2,6}\s*-\s*\d+",
    re.IGNORECASE
)


def find_orders(text):

    if not text:
        return []

    matches = ORDER_PATTERN.findall(text)

    result = []

    for value in matches:

        value = normalize_order(value)

        if value and value not in result:
            result.append(value)

    return result


# ============================================================
# ДОПОЛНИТЕЛЬНЫЙ ПОИСК
# ============================================================

def find_order_by_numeric_text(text):

    """
    Резервный способ.

    Иногда PDF может вернуть номер без дефисов:

        3496587301951

    Тогда пытаемся найти последовательность цифр.
    """

    if not text:
        return []

    clean = re.sub(
        r"\D",
        "",
        text
    )

    result = []

    # Обычно номер Ozon содержит 15 цифр.
    # Проверяем возможные окна.
    for length in [15, 14, 13, 12]:

        if len(clean) < length:
            continue

        for i in range(
            0,
            len(clean) - length + 1
        ):

            candidate = clean[
                i:i + length
            ]

            if candidate not in result:
                result.append(candidate)

    return result


# ============================================================
# ПОИСК КОЛОНОК В EXCEL / CSV
# ============================================================

def normalize_column_name(name):

    if name is None:
        return ""

    value = str(name).strip().lower()

    value = (
        value
        .replace("ё", "е")
        .replace("\xa0", " ")
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value


def find_column(columns, variants):

    normalized = {}

    for column in columns:

        normalized[
            normalize_column_name(column)
        ] = column

    # Сначала точное совпадение
    for variant in variants:

        v = normalize_column_name(
            variant
        )

        if v in normalized:
            return normalized[v]

    # Затем частичное
    for column in columns:

        c = normalize_column_name(
            column
        )

        for variant in variants:

            v = normalize_column_name(
                variant
            )

            if v in c:

                return column

    return None


# ============================================================
# ЧТЕНИЕ XLSX
# ============================================================

def read_xlsx(file):

    try:

        import openpyxl

    except ImportError:

        raise Exception(
            "Не установлен openpyxl. "
            "Добавьте openpyxl в requirements.txt"
        )

    file.seek(0)

    workbook = openpyxl.load_workbook(
        file,
        read_only=True,
        data_only=True
    )

    # Берем первый лист
    sheet = workbook[
        workbook.sheetnames[0]
    ]

    rows = sheet.iter_rows(
        values_only=True
    )

    try:
        headers = next(rows)
    except StopIteration:
        return []

    headers = [
        str(h).strip()
        if h is not None
        else ""
        for h in headers
    ]

    result = []

    for row in rows:

        item = {}

        for i, header in enumerate(headers):

            if not header:
                continue

            value = (
                row[i]
                if i < len(row)
                else ""
            )

            item[header] = (
                ""
                if value is None
                else str(value).strip()
            )

        if any(
            str(v).strip()
            for v in item.values()
        ):

            result.append(item)

    workbook.close()

    return result


# ============================================================
# ЧТЕНИЕ CSV
# ============================================================

def read_csv_file(file):

    file.seek(0)

    raw = file.read()

    # Пробуем UTF-8
    try:

        text = raw.decode(
            "utf-8-sig"
        )

    except UnicodeDecodeError:

        text = raw.decode(
            "cp1251",
            errors="replace"
        )

    # Определяем разделитель
    sample = text[:5000]

    try:

        dialect = csv.Sniffer().sniff(
            sample,
            delimiters=";,|\t"
        )

        delimiter = dialect.delimiter

    except Exception:

        delimiter = ";"

    reader = csv.DictReader(
        text.splitlines(),
        delimiter=delimiter
    )

    result = []

    for row in reader:

        clean_row = {}

        for key, value in row.items():

            if key is None:
                continue

            clean_row[
                str(key).strip()
            ] = (
                ""
                if value is None
                else str(value).strip()
            )

        if any(
            str(v).strip()
            for v in clean_row.values()
        ):

            result.append(clean_row)

    return result


# ============================================================
# ЗАГРУЗКА ТАБЛИЦЫ
# ============================================================

def load_product_table(file):

    name = file.name.lower()

    if name.endswith(
        ".xlsx"
    ):

        rows = read_xlsx(file)

    elif name.endswith(
        ".csv"
    ):

        rows = read_csv_file(file)

    else:

        raise Exception(
            "Поддерживаются только XLSX и CSV."
        )

    if not rows:

        raise Exception(
            "Таблица пустая."
        )

    return rows


# ============================================================
# СОЗДАНИЕ ИНДЕКСА ПО ОТПРАВЛЕНИЯМ
# ============================================================

def build_order_index(rows):

    if not rows:
        return {}, {}, None

    columns = list(
        rows[0].keys()
    )

    order_column = find_column(
        columns,
        [
            "Номер отправления",
            "номер отправления",
            "posting number",
            "posting_number",
            "Номер отправления Ozon",
            "Отправление"
        ]
    )

    article_column = find_column(
        columns,
        [
            "Артикул продавца",
            "Артикул",
            "offer_id",
            "Offer ID"
        ]
    )

    name_column = find_column(
        columns,
        [
            "Название товара",
            "Название",
            "Товар",
            "Наименование"
        ]
    )

    qty_column = find_column(
        columns,
        [
            "Количество",
            "Кол-во",
            "Кол во",
            "Qty",
            "quantity"
        ]
    )

    sku_column = find_column(
        columns,
        [
            "SKU Ozon",
            "SKU",
            "product_id"
        ]
    )

    if not order_column:

        raise Exception(
            "Не найдена колонка "
            "'Номер отправления'.\n\n"
            "Найденные колонки:\n" +
            "\n".join(
                str(x)
                for x in columns
            )
        )

    if not article_column:

        raise Exception(
            "Не найдена колонка "
            "'Артикул продавца'.\n\n"
            "Найденные колонки:\n" +
            "\n".join(
                str(x)
                for x in columns
            )
        )

    if not qty_column:

        raise Exception(
            "Не найдена колонка "
            "'Количество'.\n\n"
            "Найденные колонки:\n" +
            "\n".join(
                str(x)
                for x in columns
            )
        )

    index = {}

    numeric_index = {}

    duplicate_orders = []

    for row in rows:

        order = row.get(
            order_column,
            ""
        )

        order = normalize_order(
            order
        )

        if not order:
            continue

        article = (
            row.get(
                article_column,
                ""
            )
            if article_column
            else ""
        )

        name = (
            row.get(
                name_column,
                ""
            )
            if name_column
            else ""
        )

        qty = (
            row.get(
                qty_column,
                ""
            )
            if qty_column
            else ""
        )

        sku = (
            row.get(
                sku_column,
                ""
            )
            if sku_column
            else ""
        )

        item = {
            "order": order,
            "article": str(
                article
            ).strip(),

            "name": str(
                name
            ).strip(),

            "qty": str(
                qty
            ).strip(),

            "sku": str(
                sku
            ).strip()
        }

        key = order_key(
            order
        )

        if key in index:

            duplicate_orders.append(
                order
            )

        index[key] = item

        numeric_key = (
            numeric_order_key(
                order
            )
        )

        if numeric_key:

            numeric_index[
                numeric_key
            ] = item

    return (
        index,
        numeric_index,
        duplicate_orders
    )


# ============================================================
# ПОИСК ТОВАРА
# ============================================================

def find_product(
    order,
    order_index,
    numeric_index
):

    if not order:
        return None

    key = order_key(
        order
    )

    info = order_index.get(
        key
    )

    if info:
        return info

    numeric_key = (
        numeric_order_key(
            order
        )
    )

    if numeric_key:

        info = numeric_index.get(
            numeric_key
        )

        if info:
            return info

    return None


# ============================================================
# ПЕРЕНОС ТЕКСТА НА ЭТИКЕТКУ
# ============================================================

def wrap_text(
    text,
    max_chars
):

    if not text:
        return []

    words = str(text).split()

    lines = []

    current = ""

    for word in words:

        if not current:

            current = word

        elif (
            len(current) +
            len(word) +
            1
            <= max_chars
        ):

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
# СОЗДАНИЕ ИНФО-БЛОКА
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

    # --------------------------------------------------------
    # Номер отправления
    # --------------------------------------------------------

    c.setFont(
        font_name,
        9
    )

    c.drawString(
        x_margin,
        height - 17,
        f"Заказ: {order_number}"
    )

    c.line(
        x_margin,
        height - 20,
        width - x_margin,
        height - 20
    )

    # --------------------------------------------------------
    # Артикул
    # --------------------------------------------------------

    article = str(
        product_info.get(
            "article",
            "-"
        )
    )

    c.setFont(
        font_name,
        13
    )

    if len(article) > 28:

        article = (
            article[:25] +
            "..."
        )

    c.drawString(
        x_margin,
        height - 37,
        f"Арт: {article}"
    )

    # --------------------------------------------------------
    # SKU
    # --------------------------------------------------------

    sku = str(
        product_info.get(
            "sku",
            ""
        )
    )

    if sku:

        c.setFont(
            font_name,
            7
        )

        c.drawString(
            x_margin,
            height - 49,
            f"SKU: {sku}"
        )

    # --------------------------------------------------------
    # Название товара
    # --------------------------------------------------------

    name = str(
        product_info.get(
            "name",
            "Товар не найден"
        )
    )

    top_limit = (
        height - 61
    )

    bottom_limit = 55

    available_height = (
        top_limit -
        bottom_limit
    )

    font_size = 9

    line_height = 11

    lines = wrap_text(
        name,
        38
    )

    while (
        len(lines) * line_height
        > available_height
        and font_size > 5.5
    ):

        font_size -= 0.5
        line_height -= 0.5

        chars = int(
            38 *
            (9 / font_size)
        )

        lines = wrap_text(
            name,
            chars
        )

    c.setFont(
        font_name,
        font_size
    )

    y = top_limit

    for line in lines:

        if y <= bottom_limit:
            break

        c.drawString(
            x_margin,
            y,
            line
        )

        y -= line_height

    # --------------------------------------------------------
    # КОЛИЧЕСТВО
    # --------------------------------------------------------

    qty = str(
        product_info.get(
            "qty",
            "?"
        )
    )

    c.setFont(
        font_name,
        24
    )

    c.drawString(
        x_margin,
        15,
        f"КОЛ-ВО: {qty}"
    )

    c.save()

    packet.seek(0)

    from pypdf import PdfReader

    return PdfReader(
        packet
    ).pages[0]


# ============================================================
# ОСНОВНОЙ ИНТЕРФЕЙС
# ============================================================

st.divider()

col1, col2 = st.columns(2)

with col1:

    labels_file = st.file_uploader(
        "1️⃣ Этикетки Ozon PDF",
        type=["pdf"]
    )

with col2:

    table_file = st.file_uploader(
        "2️⃣ Лист подбора Ozon API",
        type=["xlsx", "csv"]
    )


# ============================================================
# ПРЕДПРОСМОТР ТАБЛИЦЫ
# ============================================================

table_rows = None

if table_file:

    try:

        table_rows = load_product_table(
            table_file
        )

        st.success(
            f"✅ Таблица прочитана: "
            f"{len(table_rows)} строк"
        )

        with st.expander(
            "🔎 Предпросмотр листа подбора"
        ):

            st.dataframe(
                table_rows[:20],
                use_container_width=True
            )

    except Exception as e:

        st.error(
            f"Ошибка чтения таблицы: {e}"
        )

        table_rows = None


# ============================================================
# КНОПКА ОБРАБОТКИ
# ============================================================

if (
    labels_file
    and table_file
    and table_rows
):

    if st.button(
        "🚀 СОЗДАТЬ ГОТОВЫЙ PDF",
        type="primary",
        use_container_width=True
    ):

        try:

            # ------------------------------------------------
            # Создаем индекс таблицы
            # ------------------------------------------------

            (
                order_index,
                numeric_index,
                duplicate_orders
            ) = build_order_index(
                table_rows
            )

            st.info(
                f"🔑 В индексе отправлений: "
                f"{len(order_index)}"
            )

            if duplicate_orders:

                st.warning(
                    "⚠️ В таблице обнаружены "
                    f"дублирующиеся номера отправлений: "
                    f"{len(duplicate_orders)}"
                )

            # ------------------------------------------------
            # Читаем PDF
            # ------------------------------------------------

            labels_file.seek(0)

            reader = PdfReader(
                labels_file
            )

            writer = PdfWriter()

            total_labels = len(
                reader.pages
            )

            success_count = 0

            error_orders = []

            progress = st.progress(
                0
            )

            status = st.empty()

            # ------------------------------------------------
            # Обрабатываем страницы
            # ------------------------------------------------

            for i, page in enumerate(
                reader.pages
            ):

                status.text(
                    f"🏷 Обработка этикетки "
                    f"{i + 1} / "
                    f"{total_labels}"
                )

                # Оригинальная страница
                writer.add_page(
                    page
                )

                # ------------------------------------------------
                # Извлекаем текст
                # ------------------------------------------------

                try:

                    text = (
                        page.extract_text()
                        or ""
                    )

                except Exception:

                    text = ""

                # ------------------------------------------------
                # Ищем полный номер
                # ------------------------------------------------

                orders = find_orders(
                    text
                )

                full_order = (
                    orders[0]
                    if orders
                    else ""
                )

                # ------------------------------------------------
                # Если стандартная регулярка не нашла
                # ------------------------------------------------

                if not full_order:

                    numeric_candidates = (
                        find_order_by_numeric_text(
                            text
                        )
                    )

                    # Пытаемся сопоставить
                    # кандидатов с нашей таблицей

                    for candidate in (
                        numeric_candidates
                    ):

                        if candidate in numeric_index:

                            info = (
                                numeric_index[
                                    candidate
                                ]
                            )

                            full_order = (
                                info["order"]
                            )

                            break

                # ------------------------------------------------
                # Ищем товар
                # ------------------------------------------------

                info = None

                if full_order:

                    info = find_product(
                        full_order,
                        order_index,
                        numeric_index
                    )

                # ------------------------------------------------
                # Если товар не найден
                # ------------------------------------------------

                if not info:

                    display_order = (
                        full_order
                        if full_order
                        else "НЕ РАСПОЗНАН"
                    )

                    info = {
                        "order": display_order,
                        "article": "-",
                        "name": "Товар не найден",
                        "qty": "?",
                        "sku": ""
                    }

                    error_orders.append(
                        display_order
                    )

                else:

                    success_count += 1

                # ------------------------------------------------
                # Размер этикетки
                # ------------------------------------------------

                width = float(
                    page.mediabox.width
                )

                height = float(
                    page.mediabox.height
                )

                # ------------------------------------------------
                # Добавляем информацию
                # ------------------------------------------------

                info_page = (
                    create_info_label(
                        width,
                        height,
                        info.get(
                            "order",
                            full_order
                        ),
                        info
                    )
                )

                writer.add_page(
                    info_page
                )

                progress.progress(
                    (i + 1) /
                    total_labels
                )

            status.text(
                "✅ Обработка завершена"
            )

            # ====================================================
            # СТАТИСТИКА
            # ====================================================

            st.divider()

            col_m1, col_m2, col_m3 = (
                st.columns(3)
            )

            col_m1.metric(
                "Всего этикеток",
                total_labels
            )

            col_m2.metric(
                "Сопоставлено",
                success_count
            )

            col_m3.metric(
                "Ошибок",
                total_labels -
                success_count
            )

            # ====================================================
            # РЕЗУЛЬТАТ
            # ====================================================

            if (
                success_count ==
                total_labels
            ):

                st.success(
                    "🎉 Все этикетки "
                    "сопоставлены с листом подбора!"
                )

            else:

                st.error(
                    "⚠️ Не удалось "
                    f"сопоставить "
                    f"{total_labels - success_count} "
                    "этикеток."
                )

                if error_orders:

                    with st.expander(
                        "❌ Проблемные отправления"
                    ):

                        for order in (
                            error_orders[:200]
                        ):

                            st.write(
                                f"• {order}"
                            )

            # ====================================================
            # СОЗДАЕМ PDF
            # ====================================================

            output = BytesIO()

            writer.write(
                output
            )

            output.seek(0)

            st.download_button(
                "📥 Скачать готовый PDF",
                output,
                "Ozon_Ready_Labels.pdf",
                "application/pdf",
                type="primary",
                use_container_width=True
            )

        except Exception as e:

            st.error(
                "❌ Ошибка обработки:\n\n"
                + str(e)
            )

            with st.expander(
                "Техническая информация"
            ):

                st.exception(e)


# ============================================================
# ПОДСКАЗКА
# ============================================================

if not labels_file or not table_file:

    st.info(
        """
        ### Как использовать

        **1.** Загрузите PDF с этикетками Ozon.

        **2.** Из Google Таблицы экспортируйте
        лист `OZON | Лист подбора API` в XLSX:

        `Файл → Скачать → Microsoft Excel (.xlsx)`

        **3.** Загрузите полученный XLSX сюда.

        **4.** Нажмите **«СОЗДАТЬ ГОТОВЫЙ PDF»**.

        Артикул и количество берутся непосредственно
        из данных Ozon API, поэтому программа больше
        не пытается угадывать их из текста листа подбора.
        """
    )
