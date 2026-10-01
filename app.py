import io
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from pypdf import PdfReader, PdfWriter

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


# ============================================================
# НАСТРОЙКИ
# ============================================================

POSTINGS_URL = (
    "https://api-seller.ozon.ru/v1/assembly/fbs/posting/list"
)

LABEL_URL = (
    "https://api-seller.ozon.ru/v2/posting/fbs/package-label"
)

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# Ozon разрешает максимум 20 posting_number
# в одном запросе этикеток.
LABEL_BATCH_SIZE = 20

REQUEST_TIMEOUT = 90

MAX_RETRIES = 3


# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(
    page_title="Ozon FBS — этикетки",
    page_icon="📦",
    layout="wide",
)

st.title("📦 Ozon FBS — отправления и этикетки")

st.caption(
    "Получение отправлений через API Ozon → "
    "получение оригинальных PDF-этикеток → "
    "точное сопоставление по FBS"
)


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header("🔐 Ozon API")

client_id = st.sidebar.text_input(
    "Client-Id",
    type="password",
)

api_key = st.sidebar.text_input(
    "Api-Key",
    type="password",
)

selected_date = st.sidebar.date_input(
    "Дата сборки",
    value=date.today(),
)

st.sidebar.markdown("---")

st.sidebar.info(
    "Этикетки берутся непосредственно из Ozon API. "
    "Оригинальная страница этикетки не изменяется."
)


# ============================================================
# HTTP HEADERS
# ============================================================

def get_headers(client_id_value, api_key_value):
    return {
        "Client-Id": str(client_id_value).strip(),
        "Api-Key": str(api_key_value).strip(),
        "Content-Type": "application/json",
    }


# ============================================================
# TIMESTAMP
# ============================================================

def get_ozon_day_range(selected_date_value):
    """
    Формирует границы дня в московском времени.

    Ozon protobuf Timestamp требует timezone.

    Например:

    2026-10-01T00:00:00+03:00
    2026-10-02T00:00:00+03:00
    """

    start = datetime(
        selected_date_value.year,
        selected_date_value.month,
        selected_date_value.day,
        0,
        0,
        0,
        tzinfo=MOSCOW_TZ,
    )

    end = start + timedelta(days=1)

    return (
        start.isoformat(),
        end.isoformat(),
    )


def get_expanded_ozon_range(selected_date_value):
    """
    Небольшое расширенное окно.

    Берём:

        выбранная дата - 1 день
        до
        выбранная дата + 2 дня

    Это позволяет не потерять отправления из-за
    особенностей cutoff/timezone.

    При этом запрос остаётся коротким.
    """

    start_date = selected_date_value - timedelta(days=1)
    end_date = selected_date_value + timedelta(days=2)

    start = datetime(
        start_date.year,
        start_date.month,
        start_date.day,
        0,
        0,
        0,
        tzinfo=MOSCOW_TZ,
    )

    end = datetime(
        end_date.year,
        end_date.month,
        end_date.day,
        0,
        0,
        0,
        tzinfo=MOSCOW_TZ,
    )

    return (
        start.isoformat(),
        end.isoformat(),
    )


# ============================================================
# БЕЗОПАСНОЕ ПРЕОБРАЗОВАНИЕ В СТРОКУ
# ============================================================

def safe_str(value):
    if value is None:
        return ""

    if isinstance(value, float):

        if value.is_integer():
            return str(int(value))

    return str(value)


# ============================================================
# ПОЛУЧЕНИЕ POSTINGS
# ============================================================

def extract_posting_items(data):
    """
    Извлекает список отправлений из ответа Ozon.

    Основной вариант:
        result.postings

    Также поддерживаем несколько вариантов структуры,
    чтобы приложение не ломалось при изменении вложенности.
    """

    if not isinstance(data, dict):
        return []

    result = data.get("result")

    if isinstance(result, list):
        return result

    if isinstance(result, dict):

        for key in (
            "postings",
            "items",
            "orders",
        ):
            value = result.get(key)

            if isinstance(value, list):
                return value

        if (
            result.get("posting_number")
            or result.get("postingNumber")
        ):
            return [result]

    for key in (
        "postings",
        "items",
        "orders",
    ):
        value = data.get(key)

        if isinstance(value, list):
            return value

    return []


def convert_posting(item):
    """
    Приводит отправление Ozon к единому формату.
    """

    posting_number = (
        item.get("posting_number")
        or item.get("postingNumber")
        or item.get("posting")
        or item.get("number")
        or ""
    )

    status = (
        item.get("status")
        or item.get("posting_status")
        or item.get("postingStatus")
        or ""
    )

    products = (
        item.get("products")
        or item.get("items")
        or item.get("product_items")
        or []
    )

    if not isinstance(products, list):
        products = []

    # --------------------------------------------------------
    # Товар
    # --------------------------------------------------------

    if products:

        product = products[0]

        article = (
            product.get("offer_id")
            or product.get("offerId")
            or product.get("sku")
            or product.get("article")
            or product.get("product_id")
            or ""
        )

        product_name = (
            product.get("name")
            or product.get("product_name")
            or product.get("productName")
            or ""
        )

        quantity = (
            product.get("quantity")
            or product.get("qty")
            or 1
        )

    else:

        article = (
            item.get("offer_id")
            or item.get("offerId")
            or item.get("sku")
            or item.get("article")
            or ""
        )

        product_name = (
            item.get("name")
            or item.get("product_name")
            or item.get("productName")
            or ""
        )

        quantity = (
            item.get("quantity")
            or item.get("qty")
            or 1
        )

    return {
        "posting_number": safe_str(posting_number).strip(),
        "status": safe_str(status).strip(),
        "article": safe_str(article).strip(),
        "product": safe_str(product_name).strip(),
        "quantity": safe_str(quantity).strip(),
        "raw": item,
    }


def get_postings(
    client_id_value,
    api_key_value,
    selected_date_value,
):
    """
    Получает отправления через:

        /v1/assembly/fbs/posting/list

    В запросе обязательно используется timezone.
    """

    headers = get_headers(
        client_id_value,
        api_key_value,
    )

    cutoff_from, cutoff_to = get_expanded_ozon_range(
        selected_date_value
    )

    all_items = []

    cursor = ""

    last_response = {}

    progress = st.progress(0)

    status_box = st.empty()

    page_number = 0

    while True:

        page_number += 1

        payload = {
            "filter": {
                "cutoff_from": cutoff_from,
                "cutoff_to": cutoff_to,
            },
            "limit": 1000,
            "sort_dir": "ASC",
        }

        if cursor:
            payload["cursor"] = cursor

        status_box.info(
            f"Получение отправлений... "
            f"страница {page_number}"
        )

        response = None

        # ----------------------------------------------------
        # RETRIES
        # ----------------------------------------------------

        for attempt in range(
            1,
            MAX_RETRIES + 1,
        ):

            try:

                response = requests.post(
                    POSTINGS_URL,
                    headers=headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )

            except requests.RequestException as exc:

                if attempt >= MAX_RETRIES:
                    raise RuntimeError(
                        "Ошибка соединения с Ozon API:\n\n"
                        f"{exc}"
                    )

                time.sleep(attempt * 2)

                continue

            # Успешно
            if response.status_code == 200:
                break

            # Временная ошибка
            if response.status_code in (
                429,
                500,
                502,
                503,
                504,
            ):

                if attempt >= MAX_RETRIES:
                    raise RuntimeError(
                        f"Ozon API HTTP "
                        f"{response.status_code}\n\n"
                        f"{response.text}"
                    )

                time.sleep(attempt * 2)

                continue

            # Ошибка запроса
            raise RuntimeError(
                f"Ozon API HTTP "
                f"{response.status_code}\n\n"
                f"{response.text}"
            )

        if response is None:
            raise RuntimeError(
                "Ozon API не вернул ответ."
            )

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------

        try:

            data = response.json()

        except ValueError:

            raise RuntimeError(
                "Ozon API вернул не JSON:\n\n"
                + response.text[:5000]
            )

        last_response = data

        page_items = extract_posting_items(data)

        if not page_items:

            if not all_items:
                return [], data

            break

        all_items.extend(page_items)

        # ----------------------------------------------------
        # CURSOR
        # ----------------------------------------------------

        next_cursor = ""

        if isinstance(
            data.get("cursor"),
            str,
        ):
            next_cursor = (
                data.get("cursor")
                or ""
            )

        result = data.get("result")

        if isinstance(result, dict):

            if isinstance(
                result.get("cursor"),
                str,
            ):
                next_cursor = (
                    result.get("cursor")
                    or next_cursor
                )

        if not next_cursor:
            break

        if next_cursor == cursor:
            break

        cursor = next_cursor

        progress.progress(
            min(
                0.95,
                0.1 + len(all_items) / 1000,
            )
        )

        if len(page_items) < 1000:
            break

    progress.progress(1.0)

    # --------------------------------------------------------
    # CONVERT
    # --------------------------------------------------------

    postings = []

    for item in all_items:

        if not isinstance(item, dict):
            continue

        posting = convert_posting(item)

        if posting["posting_number"]:
            postings.append(posting)

    # --------------------------------------------------------
    # UNIQUE
    # --------------------------------------------------------

    unique = {}

    for posting in postings:

        number = posting["posting_number"]

        if number not in unique:
            unique[number] = posting

    postings = list(unique.values())

    status_box.success(
        f"Получено уникальных отправлений: "
        f"{len(postings)}"
    )

    return postings, last_response


# ============================================================
# ПОИСК FBS В PDF
# ============================================================

def normalize_fbs(value):
    """
    Нормализация FBS.

    Пример:

        78277691-0407-1

    остаётся:

        78277691-0407-1
    """

    if not value:
        return None

    value = str(value).strip()

    value = value.replace(" ", "")

    match = re.search(
        r"([0-9]{6,20}-[0-9]{2,8}-[0-9]+)",
        value,
    )

    if not match:
        return None

    return match.group(1)


def extract_fbs_from_page(page):
    """
    Извлекает ТОЛЬКО FBS.

    Мы специально НЕ возвращаем и НЕ показываем
    весь текст страницы.

    Важно:
    даже если кириллица в PDF извлекается
    как квадраты, цифры и дефисы FBS
    обычно остаются корректными.

    Пример:

        FBS: 1123559 78277691-0407-1 7691 ...

    Результат:

        78277691-0407-1
    """

    try:
        text = page.extract_text() or ""

    except Exception:
        text = ""

    if not text:
        return None

    # --------------------------------------------------------
    # Вариант 1 — после FBS:
    #
    # FBS: 1123559 78277691-0407-1
    # --------------------------------------------------------

    patterns = [

        r"FBS\s*:\s*\d+\s+"
        r"([0-9]{6,20}-[0-9]{2,8}-[0-9]+)",

        r"FBS\s*[:\-]?\s*"
        r"(?:\d+\s+)?"
        r"([0-9]{6,20}-[0-9]{2,8}-[0-9]+)",

        # Самостоятельная структура номера.
        r"\b"
        r"([0-9]{6,20}-[0-9]{2,8}-[0-9]+)"
        r"\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:

            fbs = normalize_fbs(
                match.group(1)
            )

            if fbs:
                return fbs

    return None


# ============================================================
# РАЗДЕЛЕНИЕ ОРИГИНАЛЬНОГО PDF
# ============================================================

def split_label_pdf(pdf_bytes):
    """
    Разделяет полученный Ozon PDF на отдельные страницы.

    КРИТИЧНО:

    Мы НЕ рисуем этикетку заново.

    Каждая исходная страница добавляется
    в итоговый PDF напрямую.
    """

    reader = PdfReader(
        io.BytesIO(pdf_bytes)
    )

    result = []

    for index, page in enumerate(
        reader.pages
    ):

        # ----------------------------------------------------
        # Сохраняем оригинальную страницу.
        # ----------------------------------------------------

        writer = PdfWriter()

        writer.add_page(page)

        output = io.BytesIO()

        writer.write(output)

        page_bytes = output.getvalue()

        # ----------------------------------------------------
        # Извлекаем только FBS.
        # ----------------------------------------------------

        fbs = extract_fbs_from_page(
            page
        )

        result.append(
            {
                "fbs": fbs,
                "page_bytes": page_bytes,
                "page_number": index + 1,
            }
        )

    return result


# ============================================================
# ЗАПРОС ОДНОЙ ПАРТИИ ЭТИКЕТОК
# ============================================================

def request_label_batch(
    client_id_value,
    api_key_value,
    posting_numbers,
):
    """
    Получение PDF этикеток.

    Максимум 20 posting_number за запрос.
    """

    headers = get_headers(
        client_id_value,
        api_key_value,
    )

    payload = {
        "posting_number": posting_numbers
    }

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = requests.post(
                LABEL_URL,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

        except requests.RequestException as exc:

            last_error = str(exc)

            if attempt < MAX_RETRIES:
                time.sleep(attempt * 2)
                continue

            raise RuntimeError(
                f"Ошибка соединения: {exc}"
            )

        # ----------------------------------------------------
        # PDF
        # ----------------------------------------------------

        if response.status_code == 200:

            content_type = (
                response.headers
                .get(
                    "Content-Type",
                    "",
                )
                .lower()
            )

            if (
                response.content[:4] == b"%PDF"
                or "application/pdf"
                in content_type
            ):

                return response.content

            # Иногда вместо PDF приходит JSON.
            try:

                error_data = response.json()

            except Exception:

                error_data = (
                    response.text[:5000]
                )

            last_error = (
                "Ozon не вернул PDF:\n"
                + str(error_data)
            )

            if attempt < MAX_RETRIES:
                time.sleep(attempt * 2)
                continue

        # ----------------------------------------------------
        # TEMPORARY
        # ----------------------------------------------------

        elif response.status_code in (
            429,
            500,
            502,
            503,
            504,
        ):

            last_error = (
                f"HTTP "
                f"{response.status_code}: "
                f"{response.text[:2000]}"
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    attempt * 2
                )

                continue

        # ----------------------------------------------------
        # OTHER ERROR
        # ----------------------------------------------------

        else:

            last_error = (
                f"HTTP "
                f"{response.status_code}: "
                f"{response.text[:5000]}"
            )

            break

    raise RuntimeError(
        last_error
        or "Не удалось получить PDF."
    )


# ============================================================
# ПОЛУЧЕНИЕ ВСЕХ ЭТИКЕТОК
# ============================================================

def get_labels(
    client_id_value,
    api_key_value,
    posting_numbers,
):
    """
    Получает этикетки партиями по 20.

    Если партия не загрузилась —
    пробуем каждую этикетку отдельно.
    """

    all_labels = []

    errors = []

    batches = [
        posting_numbers[
            i:i + LABEL_BATCH_SIZE
        ]

        for i in range(
            0,
            len(posting_numbers),
            LABEL_BATCH_SIZE,
        )
    ]

    progress = st.progress(0)

    status_box = st.empty()

    total_batches = len(batches)

    for batch_index, batch in enumerate(
        batches,
        start=1,
    ):

        status_box.info(
            f"Получение этикеток: "
            f"{batch_index}/{total_batches} "
            f"({len(batch)} шт.)"
        )

        batch_success = False

        # ----------------------------------------------------
        # ПЫТАЕМСЯ ПОЛУЧИТЬ ВСЮ ПАРТИЮ
        # ----------------------------------------------------

        try:

            pdf_bytes = request_label_batch(
                client_id_value,
                api_key_value,
                batch,
            )

            pages = split_label_pdf(
                pdf_bytes
            )

            all_labels.extend(pages)

            batch_success = True

        except Exception:
            batch_success = False

        # ----------------------------------------------------
        # FALLBACK ПО ОДНОЙ
        # ----------------------------------------------------

        if not batch_success:

            for posting_number in batch:

                try:

                    pdf_bytes = request_label_batch(
                        client_id_value,
                        api_key_value,
                        [posting_number],
                    )

                    pages = split_label_pdf(
                        pdf_bytes
                    )

                    all_labels.extend(
                        pages
                    )

                except Exception as exc:

                    errors.append(
                        {
                            "Отправление":
                                posting_number,
                            "Ошибка":
                                str(exc),
                        }
                    )

        progress.progress(
            batch_index / total_batches
        )

        # Небольшая пауза.
        if batch_index < total_batches:
            time.sleep(0.5)

    progress.progress(1.0)

    status_box.success(
        f"Получено страниц этикеток: "
        f"{len(all_labels)}"
    )

    return (
        all_labels,
        errors,
    )


# ============================================================
# ПОИСК ШРИФТА
# ============================================================

def register_unicode_font():
    """
    Ищем DejaVu Sans.

    Этот шрифт нужен только для нашей
    информационной страницы.

    Оригинальная этикетка Ozon этим шрифтом
    НЕ перерисовывается.
    """

    font_candidates = [

        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",

        "/usr/share/fonts/dejavu/DejaVuSans.ttf",

        # macOS
        "/Library/Fonts/Arial.ttf",

        "/System/Library/Fonts/Supplemental/Arial.ttf",

        # Windows
        "C:/Windows/Fonts/arial.ttf",

        "C:/Windows/Fonts/Arial.ttf",
    ]

    for font_path in font_candidates:

        try:

            pdfmetrics.registerFont(
                TTFont(
                    "OzonUnicode",
                    font_path,
                )
            )

            return "OzonUnicode"

        except Exception:
            continue

    return "Helvetica"


INFO_FONT = register_unicode_font()


# ============================================================
# ПЕРЕНОС ДЛИННОГО ТЕКСТА
# ============================================================

def wrap_text(
    text,
    max_chars=75,
):
    """
    Простой перенос текста.
    """

    text = safe_str(text)

    if not text:
        return [""]

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

    return lines or [""]


# ============================================================
# ИНФОРМАЦИОННАЯ СТРАНИЦА
# ============================================================

def create_info_page(posting):
    """
    Создаёт чистую информационную страницу.

    Здесь нет текста исходной этикетки.
    Только данные API.
    """

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=A4,
    )

    width, height = A4

    left = 45

    y = height - 55

    # --------------------------------------------------------
    # Заголовок
    # --------------------------------------------------------

    c.setFont(
        INFO_FONT,
        20,
    )

    c.drawString(
        left,
        y,
        "Информация об отправлении",
    )

    y -= 35

    c.setStrokeColor(
        colors.black
    )

    c.line(
        left,
        y,
        width - left,
        y,
    )

    y -= 35

    # --------------------------------------------------------
    # Функция строки
    # --------------------------------------------------------

    def draw_field(
        title,
        value,
    ):

        nonlocal y

        c.setFont(
            INFO_FONT,
            11,
        )

        c.drawString(
            left,
            y,
            title + ":",
        )

        value_x = left + 115

        lines = wrap_text(
            value,
            65,
        )

        for line_index, line in enumerate(
            lines
        ):

            c.setFont(
                INFO_FONT,
                11,
            )

            c.drawString(
                value_x,
                y,
                line,
            )

            y -= 17

            if line_index < len(lines) - 1:
                y -= 2

        y -= 13

    # --------------------------------------------------------
    # Данные
    # --------------------------------------------------------

    draw_field(
        "Отправление",
        posting.get(
            "posting_number",
            "",
        ),
    )

    draw_field(
        "Артикул",
        posting.get(
            "article",
            "",
        ),
    )

    draw_field(
        "Товар",
        posting.get(
            "product",
            "",
        ),
    )

    draw_field(
        "Количество",
        posting.get(
            "quantity",
            "",
        ),
    )

    draw_field(
        "Статус",
        posting.get(
            "status",
            "",
        ),
    )

    # --------------------------------------------------------
    # Нижняя отметка
    # --------------------------------------------------------

    c.setFont(
        INFO_FONT,
        9,
    )

    c.setFillColor(
        colors.grey
    )

    c.drawString(
        left,
        30,
        "Данные получены из Ozon Seller API",
    )

    c.save()

    buffer.seek(0)

    return buffer.getvalue()


# ============================================================
# ИТОГОВЫЙ PDF
# ============================================================

def build_final_pdf(mapped_labels):
    """
    Создаёт итоговый PDF.

    Для каждой отправки:

        [ОРИГИНАЛЬНАЯ ЭТИКЕТКА OZON]
                       +
        [ИНФОРМАЦИЯ ИЗ API]

    Оригинальная этикетка НЕ перерисовывается.
    """

    writer = PdfWriter()

    for item in mapped_labels:

        # ====================================================
        # 1. ОРИГИНАЛЬНАЯ ЭТИКЕТКА
        # ====================================================

        label_reader = PdfReader(
            io.BytesIO(
                item["page_bytes"]
            )
        )

        for page in label_reader.pages:

            # Добавляем страницу как есть.
            writer.add_page(page)

        # ====================================================
        # 2. ИНФОРМАЦИОННАЯ СТРАНИЦА
        # ====================================================

        info_bytes = create_info_page(
            item["posting"]
        )

        info_reader = PdfReader(
            io.BytesIO(info_bytes)
        )

        for page in info_reader.pages:

            writer.add_page(page)

    output = io.BytesIO()

    writer.write(output)

    return output.getvalue()


# ============================================================
# ОСНОВНОЙ ПРОЦЕСС
# ============================================================

if st.button(
    "🚀 Получить отправления и этикетки",
    type="primary",
    use_container_width=True,
):

    # ========================================================
    # ПРОВЕРКА API
    # ========================================================

    if not client_id.strip():

        st.error(
            "Введите Client-Id."
        )

        st.stop()

    if not api_key.strip():

        st.error(
            "Введите Api-Key."
        )

        st.stop()

    # ========================================================
    # 1. ОТПРАВЛЕНИЯ
    # ========================================================

    st.subheader(
        "1️⃣ Отправления"
    )

    try:

        postings, raw_response = get_postings(
            client_id.strip(),
            api_key.strip(),
            selected_date,
        )

    except Exception as exc:

        st.error(
            f"Ошибка получения отправлений:\n\n"
            f"{exc}"
        )

        st.stop()

    if not postings:

        st.warning(
            "Ozon не вернул отправления "
            "за запрошенный период."
        )

        if raw_response:

            with st.expander(
                "🔧 Технический ответ Ozon"
            ):

                st.json(
                    raw_response
                )

        st.stop()

    # ========================================================
    # MAP
    # ========================================================

    postings_map = {
        p["posting_number"]: p
        for p in postings
        if p["posting_number"]
    }

    st.success(
        f"Найдено отправлений: "
        f"{len(postings_map)}"
    )

    # ========================================================
    # 2. ЭТИКЕТКИ
    # ========================================================

    st.subheader(
        "2️⃣ Этикетки Ozon"
    )

    posting_numbers = list(
        postings_map.keys()
    )

    try:

        labels, label_errors = get_labels(
            client_id.strip(),
            api_key.strip(),
            posting_numbers,
        )

    except Exception as exc:

        st.error(
            f"Ошибка получения этикеток:\n\n"
            f"{exc}"
        )

        st.stop()

    if not labels:

        st.error(
            "Не удалось получить ни одной "
            "страницы этикеток."
        )

        if label_errors:

            st.dataframe(
                label_errors,
                use_container_width=True,
                hide_index=True,
            )

        st.stop()

    # ========================================================
    # 3. ТОЧНЫЙ МАППИНГ
    # ========================================================

    st.subheader(
        "3️⃣ Сопоставление по FBS"
    )

    mapped_labels = []

    unknown_labels = []

    duplicate_labels = []

    seen_fbs = set()

    recognized_fbs = 0

    # --------------------------------------------------------
    # Каждая страница PDF анализируется отдельно.
    # --------------------------------------------------------

    for label in labels:

        fbs = label.get(
            "fbs"
        )

        # ----------------------------------------------------
        # FBS не найден
        # ----------------------------------------------------

        if not fbs:

            unknown_labels.append(
                {
                    "Страница":
                        label["page_number"],
                    "Причина":
                        "FBS не распознан",
                }
            )

            continue

        recognized_fbs += 1

        # ----------------------------------------------------
        # Дубликат
        # ----------------------------------------------------

        if fbs in seen_fbs:

            duplicate_labels.append(
                {
                    "FBS":
                        fbs,
                    "Страница":
                        label["page_number"],
                }
            )

            continue

        seen_fbs.add(fbs)

        # ----------------------------------------------------
        # Ищем соответствующее отправление
        # ----------------------------------------------------

        posting = postings_map.get(
            fbs
        )

        if not posting:

            unknown_labels.append(
                {
                    "FBS":
                        fbs,
                    "Страница":
                        label["page_number"],
                    "Причина":
                        "FBS отсутствует среди "
                        "полученных отправлений API",
                }
            )

            continue

        # ----------------------------------------------------
        # УСПЕШНЫЙ МАППИНГ
        # ----------------------------------------------------

        mapped_labels.append(
            {
                "fbs":
                    fbs,

                "page_bytes":
                    label["page_bytes"],

                "page_number":
                    label["page_number"],

                "posting":
                    posting,
            }
        )

    # ========================================================
    # 4. СТАТИСТИКА
    # ========================================================

    col1, col2, col3, col4 = st.columns(4)

    with col1:

        st.metric(
            "Отправлений API",
            len(postings_map),
        )

    with col2:

        st.metric(
            "Страниц этикеток",
            len(labels),
        )

    with col3:

        st.metric(
            "FBS распознано",
            recognized_fbs,
        )

    with col4:

        st.metric(
            "Сопоставлено",
            len(mapped_labels),
        )

    # ========================================================
    # 5. ОСНОВНАЯ ТАБЛИЦА
    # ========================================================

    if mapped_labels:

        table_rows = []

        for item in mapped_labels:

            posting = item["posting"]

            table_rows.append(
                {
                    "Отправление":
                        posting["posting_number"],

                    "Артикул":
                        posting["article"],

                    "Товар":
                        posting["product"],

                    "Кол-во":
                        posting["quantity"],

                    "Статус":
                        posting["status"],

                    "FBS":
                        item["fbs"],

                    "Страница":
                        item["page_number"],
                }
            )

        st.dataframe(
            table_rows,
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.warning(
            "Не удалось сопоставить "
            "ни одной этикетки."
        )

    # ========================================================
    # 6. НЕРАСПОЗНАННЫЕ
    # ========================================================

    if unknown_labels:

        with st.expander(
            "⚠️ Несопоставленные страницы "
            f"({len(unknown_labels)})"
        ):

            st.dataframe(
                unknown_labels,
                use_container_width=True,
                hide_index=True,
            )

    # ========================================================
    # 7. ОШИБКИ API ЭТИКЕТОК
    # ========================================================

    if label_errors:

        with st.expander(
            "⚠️ Ошибки получения этикеток "
            f"({len(label_errors)})"
        ):

            st.dataframe(
                label_errors,
                use_container_width=True,
                hide_index=True,
            )

    # ========================================================
    # 8. ДУБЛИКАТЫ
    # ========================================================

    if duplicate_labels:

        with st.expander(
            "⚠️ Дубликаты FBS "
            f"({len(duplicate_labels)})"
        ):

            st.dataframe(
                duplicate_labels,
                use_container_width=True,
                hide_index=True,
            )

    # ========================================================
    # 9. ИТОГОВЫЙ PDF
    # ========================================================

    if mapped_labels:

        st.subheader(
            "4️⃣ Итоговый PDF"
        )

        with st.spinner(
            "Формируем PDF..."
        ):

            final_pdf = build_final_pdf(
                mapped_labels
            )

        total_pages = (
            len(mapped_labels) * 2
        )

        st.success(
            f"Готово: "
            f"{len(mapped_labels)} отправлений, "
            f"{total_pages} страниц."
        )

        filename = (
            "ozon_fbs_"
            f"{selected_date.strftime('%Y-%m-%d')}"
            ".pdf"
        )

        st.download_button(
            label="📥 Скачать итоговый PDF",
            data=final_pdf,
            file_name=filename,
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )

    # ========================================================
    # 10. ДИАГНОСТИКА
    # ========================================================

    with st.expander(
        "🔧 Диагностика"
    ):

        exact_from, exact_to = (
            get_ozon_day_range(
                selected_date
            )
        )

        expanded_from, expanded_to = (
            get_expanded_ozon_range(
                selected_date
            )
        )

        st.write(
            f"Выбранная дата: "
            f"`{selected_date}`"
        )

        st.write(
            "Точный диапазон дня:"
        )

        st.code(
            f"{exact_from}\n{exact_to}"
        )

        st.write(
            "Фактический диапазон запроса:"
        )

        st.code(
            f"{expanded_from}\n{expanded_to}"
        )

        st.write(
            f"Отправлений API: "
            f"{len(postings_map)}"
        )

        st.write(
            f"Страниц этикеток: "
            f"{len(labels)}"
        )

        st.write(
            f"FBS распознано: "
            f"{recognized_fbs}"
        )

        st.write(
            f"Сопоставлено: "
            f"{len(mapped_labels)}"
        )

        st.write(
            f"Без сопоставления: "
            f"{len(unknown_labels)}"
        )

        if mapped_labels:

            st.write(
                "Примеры найденных FBS:"
            )

            examples = [
                item["fbs"]
                for item in mapped_labels[:10]
            ]

            st.code(
                "\n".join(examples)
            )
