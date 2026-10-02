import streamlit as st
import requests
import pandas as pd
import io
import re
import time
import os
import tempfile

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm


# ============================================================
# НАСТРОЙКИ
# ============================================================

OZON_API_URL = "https://api-seller.ozon.ru"

CREATE_LABELS_URL = (
    f"{OZON_API_URL}/v3/posting/fbs/package-label/create"
)

GET_LABELS_URL = (
    f"{OZON_API_URL}/v2/posting/fbs/package-label/get"
)

MAX_POSTINGS_PER_REQUEST = 1000

POLL_INTERVAL = 5
MAX_POLL_SECONDS = 180


# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(
    page_title="Ozon FBS — Этикетки",
    page_icon="🟠",
    layout="wide"
)

st.title("🟠 Ozon FBS — автоматические этикетки")

st.caption(
    "Google Sheets → Ozon API → настоящие этикетки Ozon → "
    "артикул + количество"
)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def normalize_text(value):
    if value is None:
        return ""

    return str(value).strip()


def normalize_posting_number(value):
    """
    Нормализует номер отправления.

    Например:

    87180955-0554-25
    87180955-0554-25

    остаётся без изменений.
    """
    if value is None:
        return ""

    s = str(value).strip()

    # Excel иногда превращает значения в float
    if s.endswith(".0"):
        s = s[:-2]

    s = s.replace(" ", "")
    s = s.replace("–", "-")
    s = s.replace("—", "-")

    return s


def find_column(df, variants, required=True):
    """
    Ищет колонку по нескольким возможным названиям.
    """

    normalized = {}

    for col in df.columns:
        key = (
            str(col)
            .strip()
            .lower()
            .replace("\n", " ")
        )
        normalized[key] = col

    for variant in variants:
        variant_key = (
            variant
            .strip()
            .lower()
            .replace("\n", " ")
        )

        if variant_key in normalized:
            return normalized[variant_key]

    # Более мягкий поиск
    for col in df.columns:

        col_text = (
            str(col)
            .strip()
            .lower()
            .replace("\n", " ")
        )

        for variant in variants:

            variant_key = (
                variant
                .strip()
                .lower()
            )

            if variant_key in col_text:
                return col

    if required:
        raise ValueError(
            "Не найдена необходимая колонка. "
            f"Искал: {', '.join(variants)}"
        )

    return None


def load_table(uploaded_file):

    name = uploaded_file.name.lower()

    if name.endswith(".xlsx") or name.endswith(".xls"):
        df = pd.read_excel(uploaded_file)

    elif name.endswith(".csv"):
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
                sep=";",
                encoding="utf-8-sig"
            )

    else:
        raise ValueError(
            "Загрузите XLSX, XLS или CSV."
        )

    # Удаляем полностью пустые строки
    df = df.dropna(
        how="all"
    ).reset_index(drop=True)

    return df


# ============================================================
# ПОИСК КОЛОНОК
# ============================================================

def prepare_api_table(df):

    posting_col = find_column(
        df,
        [
            "Номер отправления",
            "Номер отправления ",
            "posting_number",
            "Posting Number"
        ]
    )

    article_col = find_column(
        df,
        [
            "Артикул продавца",
            "Артикул",
            "seller_article",
            "offer_id"
        ]
    )

    name_col = find_column(
        df,
        [
            "Название товара",
            "Наименование товара",
            "Название",
            "product_name"
        ],
        required=False
    )

    quantity_col = find_column(
        df,
        [
            "Количество",
            "Кол-во",
            "Кол",
            "quantity"
        ]
    )

    sku_col = find_column(
        df,
        [
            "SKU Ozon",
            "SKU",
            "sku"
        ],
        required=False
    )

    prepared = []

    for _, row in df.iterrows():

        posting = normalize_posting_number(
            row.get(posting_col, "")
        )

        if not posting:
            continue

        article = normalize_text(
            row.get(article_col, "")
        )

        product_name = ""

        if name_col:
            product_name = normalize_text(
                row.get(name_col, "")
            )

        quantity_raw = row.get(
            quantity_col,
            1
        )

        try:
            quantity = float(quantity_raw)

            if quantity.is_integer():
                quantity = int(quantity)

        except Exception:
            quantity = quantity_raw

        sku = ""

        if sku_col:
            sku = normalize_text(
                row.get(sku_col, "")
            )

        prepared.append(
            {
                "posting_number": posting,
                "article": article,
                "product_name": product_name,
                "quantity": quantity,
                "sku": sku,
            }
        )

    return prepared


# ============================================================
# OZON API
# ============================================================

def ozon_headers(client_id, api_key):

    return {
        "Client-Id": str(client_id).strip(),
        "Api-Key": str(api_key).strip(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def create_label_task(
    client_id,
    api_key,
    posting_numbers
):

    headers = ozon_headers(
        client_id,
        api_key
    )

    payload = {
        "posting_numbers": posting_numbers
    }

    response = requests.post(
        CREATE_LABELS_URL,
        headers=headers,
        json=payload,
        timeout=60
    )

    if response.status_code != 200:

        try:
            error_data = response.json()
        except Exception:
            error_data = response.text

        raise RuntimeError(
            "Ошибка Ozon при создании этикеток.\n\n"
            f"HTTP {response.status_code}\n"
            f"{error_data}"
        )

    data = response.json()

    return data


def get_label_task(
    client_id,
    api_key,
    task_id
):

    headers = ozon_headers(
        client_id,
        api_key
    )

    payload = {
        "task_id": int(task_id)
    }

    response = requests.post(
        GET_LABELS_URL,
        headers=headers,
        json=payload,
        timeout=60
    )

    if response.status_code != 200:

        try:
            error_data = response.json()
        except Exception:
            error_data = response.text

        raise RuntimeError(
            "Ошибка Ozon при получении этикеток.\n\n"
            f"HTTP {response.status_code}\n"
            f"{error_data}"
        )

    return response.json()


def download_file(file_url):

    response = requests.get(
        file_url,
        timeout=120
    )

    if response.status_code != 200:

        raise RuntimeError(
            "Не удалось скачать PDF этикеток.\n"
            f"HTTP {response.status_code}"
        )

    return response.content


# ============================================================
# СОЗДАНИЕ ЗАДАНИЯ
# ============================================================

def create_ozon_labels(
    client_id,
    api_key,
    posting_numbers,
    progress_callback=None
):

    all_pdf_parts = []

    total = len(posting_numbers)

    # Ozon позволяет до 1000 отправлений
    # в новом create.
    for batch_start in range(
        0,
        total,
        MAX_POSTINGS_PER_REQUEST
    ):

        batch = posting_numbers[
            batch_start:
            batch_start + MAX_POSTINGS_PER_REQUEST
        ]

        if progress_callback:
            progress_callback(
                f"Создание задания: "
                f"{batch_start + 1}-{batch_start + len(batch)} "
                f"из {total}"
            )

        task_response = create_label_task(
            client_id,
            api_key,
            batch
        )

        tasks = (
            task_response
            .get("result", {})
            .get("tasks", [])
        )

        # Иногда API может вернуть tasks напрямую
        if not tasks:
            tasks = task_response.get(
                "tasks",
                []
            )

        if not tasks:

            raise RuntimeError(
                "Ozon не вернул задания на формирование этикеток.\n\n"
                f"Ответ API:\n{task_response}"
            )

        # Нас интересует большая этикетка.
        big_tasks = [
            task
            for task in tasks
            if task.get("task_type") == "big_label"
        ]

        # Если big_label отсутствует,
        # берём первое задание.
        if big_tasks:
            selected_tasks = big_tasks
        else:
            selected_tasks = tasks[:1]

        for task in selected_tasks:

            task_id = task.get("task_id")

            if not task_id:
                continue

            if progress_callback:
                progress_callback(
                    f"Ожидание формирования этикеток. "
                    f"Task ID: {task_id}"
                )

            start_time = time.time()

            last_status = None

            while True:

                elapsed = (
                    time.time() - start_time
                )

                if elapsed > MAX_POLL_SECONDS:

                    raise RuntimeError(
                        "Ozon слишком долго формирует "
                        f"этикетки. Task ID: {task_id}"
                    )

                result = get_label_task(
                    client_id,
                    api_key,
                    task_id
                )

                status = (
                    result
                    .get("status", {})
                )

                if isinstance(status, dict):

                    status_code = (
                        status.get("code")
                        or status.get("status")
                        or ""
                    )

                else:
                    status_code = str(
                        status
                    )

                if status_code != last_status:

                    if progress_callback:
                        progress_callback(
                            f"Task {task_id}: "
                            f"{status_code or 'unknown'}"
                        )

                    last_status = status_code

                # Есть готовый файл
                file_url = result.get(
                    "file_url"
                )

                if file_url:

                    pdf_data = download_file(
                        file_url
                    )

                    all_pdf_parts.append(
                        pdf_data
                    )

                    break

                # Иногда статус может быть внутри result
                result_obj = result.get(
                    "result"
                )

                if isinstance(result_obj, dict):

                    file_url = result_obj.get(
                        "file_url"
                    )

                    if file_url:

                        pdf_data = download_file(
                            file_url
                        )

                        all_pdf_parts.append(
                            pdf_data
                        )

                        break

                if (
                    status_code.lower()
                    in (
                        "error",
                        "failed"
                    )
                ):

                    raise RuntimeError(
                        "Ozon завершил формирование "
                        "этикеток с ошибкой.\n\n"
                        f"{result}"
                    )

                time.sleep(
                    POLL_INTERVAL
                )

    if not all_pdf_parts:

        raise RuntimeError(
            "Ozon не вернул ни одного PDF-файла."
        )

    return all_pdf_parts


# ============================================================
# PDF
# ============================================================

def get_pdf_pages(pdf_data):

    reader = PdfReader(
        io.BytesIO(pdf_data)
    )

    return reader


def extract_pdf_text(page):

    try:
        text = page.extract_text()
    except Exception:
        text = ""

    return text or ""


# ============================================================
# РАСПОЗНАВАНИЕ НОМЕРА ЭТИКЕТКИ
# ============================================================

def extract_label_key_from_text(text):

    """
    Поддерживает два основных варианта.

    1.
    Обычная этикетка:

    0124387819-0112-1

    Первый блок:
    0124387819

    Последние 4:
    7819


    2.
    Новая этикетка:

    II5010320 2549

    Ключ:
    2549
    """

    if not text:
        return None

    text = text.replace(
        "\u00a0",
        " "
    )

    # --------------------------------------------------------
    # НОВЫЙ ФОРМАТ
    #
    # II5010320 2549
    # II 5010320 2549
    # --------------------------------------------------------

    new_patterns = [
        r"\bII\s*\d{6,12}\s+(\d{4})\b",
        r"\bll\s*\d{6,12}\s+(\d{4})\b",
        r"\bИI\s*\d{6,12}\s+(\d{4})\b",
    ]

    for pattern in new_patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:

            return match.group(1)

    # --------------------------------------------------------
    # ОБЫЧНЫЙ ФОРМАТ
    #
    # 0124387819-0112-1
    #
    # Берём последние 4 цифры ПЕРВОГО блока.
    # --------------------------------------------------------

    patterns = [
        r"\b(\d{6,15})-(\d{2,6})-(\d+)\b",
        r"\b(\d{6,15})\s*-\s*(\d{2,6})\s*-\s*(\d+)\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text
        )

        if match:

            first_block = match.group(1)

            return first_block[-4:]

    # --------------------------------------------------------
    # Резервный вариант:
    #
    # ищем 4 цифры после большого цифрового блока.
    # --------------------------------------------------------

    fallback_patterns = [
        r"\b\d{6,12}\s+(\d{4})\b",
    ]

    for pattern in fallback_patterns:

        match = re.search(
            pattern,
            text
        )

        if match:

            return match.group(1)

    return None


# ============================================================
# КЛЮЧ ЭТИКЕТКИ ИЗ НОМЕРА ОТПРАВЛЕНИЯ
# ============================================================

def extract_normal_label_key(posting_number):

    """
    Для обычного отправления:

    0124387819-0112-1
             ↓
           7819
    """

    posting_number = normalize_posting_number(
        posting_number
    )

    match = re.match(
        r"^(\d+)-",
        posting_number
    )

    if not match:
        return None

    first_block = match.group(1)

    if len(first_block) < 4:
        return None

    return first_block[-4:]


# ============================================================
# СОЗДАНИЕ ИНФОРМАЦИОННОЙ СТРАНИЦЫ
# ============================================================

def register_fonts():

    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]

    bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]

    regular = None
    bold = None

    for path in font_candidates:

        if os.path.exists(path):

            regular = path
            break

    for path in bold_candidates:

        if os.path.exists(path):

            bold = path
            break

    if regular:

        try:
            pdfmetrics.registerFont(
                TTFont(
                    "AppRegular",
                    regular
                )
            )
        except Exception:
            pass

    if bold:

        try:
            pdfmetrics.registerFont(
                TTFont(
                    "AppBold",
                    bold
                )
            )
        except Exception:
            pass


register_fonts()


def get_font_regular():

    try:
        pdfmetrics.getFont(
            "AppRegular"
        )

        return "AppRegular"

    except Exception:

        return "Helvetica"


def get_font_bold():

    try:
        pdfmetrics.getFont(
            "AppBold"
        )

        return "AppBold"

    except Exception:

        return "Helvetica-Bold"


def wrap_text(
    text,
    max_chars=70
):

    text = normalize_text(
        text
    )

    if not text:
        return []

    words = text.split()

    lines = []
    current = ""

    for word in words:

        test = (
            word
            if not current
            else current + " " + word
        )

        if len(test) <= max_chars:

            current = test

        else:

            if current:
                lines.append(
                    current
                )

            current = word

    if current:
        lines.append(
            current
        )

    return lines


def create_info_page(
    item,
    label_key=None
):

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    width, height = A4

    font_regular = get_font_regular()
    font_bold = get_font_bold()

    margin = 15 * mm

    y = height - margin

    # Заголовок
    c.setFont(
        font_bold,
        18
    )

    c.drawString(
        margin,
        y,
        "OZON FBS"
    )

    y -= 12 * mm

    c.setFont(
        font_bold,
        14
    )

    c.drawString(
        margin,
        y,
        "ИНФОРМАЦИЯ ДЛЯ СБОРКИ"
    )

    y -= 15 * mm

    # Номер отправления
    c.setFont(
        font_bold,
        11
    )

    c.drawString(
        margin,
        y,
        "Номер отправления:"
    )

    y -= 7 * mm

    c.setFont(
        font_regular,
        16
    )

    c.drawString(
        margin,
        y,
        str(item["posting_number"])
    )

    y -= 12 * mm

    # Номер этикетки
    c.setFont(
        font_bold,
        11
    )

    c.drawString(
        margin,
        y,
        "Номер этикетки:"
    )

    y -= 7 * mm

    c.setFont(
        font_regular,
        22
    )

    c.drawString(
        margin,
        y,
        str(label_key or "—")
    )

    y -= 15 * mm

    # Артикул
    c.setFont(
        font_bold,
        12
    )

    c.drawString(
        margin,
        y,
        "АРТИКУЛ:"
    )

    y -= 10 * mm

    c.setFont(
        font_bold,
        26
    )

    c.drawString(
        margin,
        y,
        str(
            item["article"]
            or "НЕ УКАЗАН"
        )
    )

    y -= 18 * mm

    # Количество
    c.setFont(
        font_bold,
        12
    )

    c.drawString(
        margin,
        y,
        "КОЛИЧЕСТВО:"
    )

    y -= 12 * mm

    c.setFont(
        font_bold,
        42
    )

    c.drawString(
        margin,
        y,
        str(item["quantity"])
    )

    y -= 20 * mm

    # Название
    c.setFont(
        font_bold,
        11
    )

    c.drawString(
        margin,
        y,
        "ТОВАР:"
    )

    y -= 8 * mm

    c.setFont(
        font_regular,
        10
    )

    for line in wrap_text(
        item["product_name"],
        75
    ):

        c.drawString(
            margin,
            y,
            line
        )

        y -= 5 * mm

        if y < 20 * mm:
            break

    # SKU
    if item.get("sku"):

        y -= 5 * mm

        c.setFont(
            font_bold,
            10
        )

        c.drawString(
            margin,
            y,
            f"SKU Ozon: {item['sku']}"
        )

    c.save()

    buffer.seek(0)

    return PdfReader(
        buffer
    ).pages[0]


# ============================================================
# СКЛЕЙКА
# ============================================================

def build_output_pdf(
    pdf_parts,
    items
):

    writer = PdfWriter()

    all_label_pages = []

    # Читаем все PDF, полученные от Ozon
    for pdf_data in pdf_parts:

        reader = PdfReader(
            io.BytesIO(pdf_data)
        )

        for page in reader.pages:

            all_label_pages.append(
                page
            )

    if not all_label_pages:

        raise RuntimeError(
            "В PDF от Ozon нет страниц."
        )

    # --------------------------------------------------------
    # ВАЖНО:
    #
    # Новый API возвращает этикетки в порядке отправлений,
    # переданных в posting_numbers.
    #
    # Мы передаём только отправления, для которых
    # этикетки действительно должны быть сформированы.
    # --------------------------------------------------------

    if len(all_label_pages) != len(items):

        st.warning(
            "Количество страниц этикеток Ozon "
            "не совпало с количеством отправлений.\n\n"
            f"Этикеток: {len(all_label_pages)}\n"
            f"Отправлений: {len(items)}\n\n"
            "Будет использовано сопоставление "
            "по порядку страниц."
        )

    count = min(
        len(all_label_pages),
        len(items)
    )

    for i in range(count):

        label_page = all_label_pages[i]

        item = items[i]

        # Пытаемся получить текст
        text = extract_pdf_text(
            label_page
        )

        # Пытаемся определить ключ
        label_key = extract_label_key_from_text(
            text
        )

        # Если новый формат не удалось распознать,
        # пробуем обычный ключ из номера отправления.
        if not label_key:

            label_key = extract_normal_label_key(
                item["posting_number"]
            )

        # ----------------------------------------------------
        # Сначала оригинальная этикетка Ozon
        # ----------------------------------------------------

        writer.add_page(
            label_page
        )

        # ----------------------------------------------------
        # Затем наша информационная страница
        # ----------------------------------------------------

        info_page = create_info_page(
            item,
            label_key
        )

        writer.add_page(
            info_page
        )

    output = io.BytesIO()

    writer.write(
        output
    )

    output.seek(0)

    return output.getvalue()


# ============================================================
# ОТДЕЛЬНАЯ ФУНКЦИЯ ДЛЯ АНАЛИЗА PDF
# ============================================================

def analyze_label_pdf(
    pdf_data
):

    reader = PdfReader(
        io.BytesIO(pdf_data)
    )

    result = []

    for index, page in enumerate(
        reader.pages,
        start=1
    ):

        text = extract_pdf_text(
            page
        )

        key = extract_label_key_from_text(
            text
        )

        result.append(
            {
                "Страница": index,
                "Номер этикетки": key or "НЕ НАЙДЕН",
                "Текст": text[:500]
            }
        )

    return result


# ============================================================
# UI
# ============================================================

st.divider()

st.subheader(
    "1. Доступ к Ozon API"
)

col1, col2 = st.columns(2)

with col1:

    client_id = st.text_input(
        "Client-Id",
        type="password",
        placeholder="Введите Client-Id Ozon"
    )

with col2:

    api_key = st.text_input(
        "Api-Key",
        type="password",
        placeholder="Введите Api-Key Ozon"
    )


st.divider()

st.subheader(
    "2. Лист подбора из Google Sheets"
)

st.write(
    "Загрузите XLSX/CSV, который содержит "
    "выгрузку `OZON | Лист подбора API`."
)

uploaded_table = st.file_uploader(
    "Excel / CSV",
    type=[
        "xlsx",
        "xls",
        "csv"
    ],
    key="table_upload"
)


if uploaded_table:

    try:

        df = load_table(
            uploaded_table
        )

        items = prepare_api_table(
            df
        )

        st.success(
            f"Загружено отправлений: {len(items)}"
        )

        preview_rows = []

        for item in items[:20]:

            preview_rows.append(
                {
                    "Номер отправления":
                        item["posting_number"],

                    "Артикул":
                        item["article"],

                    "Количество":
                        item["quantity"],

                    "SKU":
                        item["sku"],

                    "Название":
                        item["product_name"],
                }
            )

        st.dataframe(
            pd.DataFrame(
                preview_rows
            ),
            use_container_width=True,
            hide_index=True
        )

        st.info(
            "В API будут переданы именно эти "
            "номера отправлений."
        )

    except Exception as e:

        st.error(
            f"Ошибка чтения таблицы: {e}"
        )

        items = []


st.divider()

st.subheader(
    "3. Получить настоящие этикетки Ozon"
)

if uploaded_table and client_id and api_key and items:

    st.write(
        f"Будет запрошено этикеток: "
        f"**{len(items)}**"
    )

    start_button = st.button(
        "🟠 Получить этикетки из Ozon",
        type="primary",
        use_container_width=True
    )

    if start_button:

        progress = st.progress(
            0
        )

        status_box = st.empty()

        try:

            # ------------------------------------------------
            # Убираем дубликаты, сохраняя порядок
            # ------------------------------------------------

            unique_items = []

            seen = set()

            for item in items:

                posting = item[
                    "posting_number"
                ]

                if posting in seen:
                    continue

                seen.add(
                    posting
                )

                unique_items.append(
                    item
                )

            if len(unique_items) != len(items):

                st.warning(
                    "В таблице были дубликаты "
                    "номеров отправлений. "
                    "Дубликаты исключены."
                )

            items_for_api = unique_items

            posting_numbers = [
                item[
                    "posting_number"
                ]
                for item in items_for_api
            ]

            status_box.info(
                "Начинаем получение этикеток..."
            )

            progress.progress(
                5
            )

            # ------------------------------------------------
            # ВАЖНО:
            #
            # Ozon требует awaiting_deliver.
            # Если часть отправлений уже отгружена,
            # API может вернуть ошибку.
            # ------------------------------------------------

            pdf_parts = create_ozon_labels(
                client_id,
                api_key,
                posting_numbers,
                progress_callback=lambda msg:
                    status_box.info(msg)
            )

            progress.progress(
                75
            )

            status_box.success(
                "Этикетки получены от Ozon."
            )

            # ------------------------------------------------
            # Формируем итоговый PDF
            # ------------------------------------------------

            status_box.info(
                "Собираем итоговый PDF..."
            )

            output_pdf = build_output_pdf(
                pdf_parts,
                items_for_api
            )

            progress.progress(
                100
            )

            st.success(
                "Готово!"
            )

            st.download_button(
                label="⬇️ Скачать готовый PDF",
                data=output_pdf,
                file_name=(
                    "Ozon_FBS_Этикетки_Готово.pdf"
                ),
                mime="application/pdf",
                use_container_width=True
            )

            # ------------------------------------------------
            # Показываем статистику
            # ------------------------------------------------

            reader = PdfReader(
                io.BytesIO(output_pdf)
            )

            st.write(
                f"**Страниц в итоговом PDF:** "
                f"{len(reader.pages)}"
            )

            st.caption(
                "На каждую этикетку Ozon добавлена "
                "информационная страница с артикулом "
                "и количеством из Google Sheets."
            )

        except Exception as e:

            progress.progress(
                100
            )

            st.error(
                "Не удалось получить этикетки."
            )

            st.exception(e)

else:

    st.info(
        "Введите Client-Id и Api-Key, "
        "загрузите лист подбора и нажмите "
        "«Получить этикетки из Ozon»."
    )


# ============================================================
# ТЕСТ РАСПОЗНАВАНИЯ
# ============================================================

st.divider()

with st.expander(
    "🔎 Тест номера этикетки"
):

    test_text = st.text_area(
        "Вставь сюда текст с этикетки",
        placeholder=(
            "Например:\n"
            "0124387819-0112-1\n\n"
            "или:\n"
            "II5010320 2549"
        )
    )

    if st.button(
        "Проверить номер"
    ):

        key = extract_label_key_from_text(
            test_text
        )

        if key:

            st.success(
                f"Номер этикетки: **{key}**"
            )

        else:

            st.error(
                "Номер этикетки не найден."
            )
