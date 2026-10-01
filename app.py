import io
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import streamlit as st

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


# ============================================================
# НАСТРОЙКИ
# ============================================================

POSTINGS_URL = "https://api-seller.ozon.ru/v1/assembly/fbs/posting/list"
LABEL_URL = "https://api-seller.ozon.ru/v2/posting/fbs/package-label"

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

LABEL_BATCH_SIZE = 20
REQUEST_TIMEOUT = 60
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
    "Получение отправлений через API Ozon → получение PDF-этикеток → "
    "точный маппинг по FBS-номеру"
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

st.sidebar.caption(
    "Этикетки запрашиваются через /v2/posting/fbs/package-label "
    "пачками до 20 отправлений."
)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def get_headers(client_id: str, api_key: str) -> dict:
    return {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }


def get_ozon_day_range(selected_date: date):
    """
    Границы выбранного дня в московском времени.

    Ozon protobuf Timestamp требует timezone.

    Пример:

    2026-09-30T00:00:00+03:00
    2026-10-01T00:00:00+03:00
    """

    start = datetime(
        selected_date.year,
        selected_date.month,
        selected_date.day,
        0,
        0,
        0,
        tzinfo=MOSCOW_TZ,
    )

    end = start + timedelta(days=1)

    return start.isoformat(), end.isoformat()


def get_expanded_ozon_range(selected_date: date):
    """
    Небольшое расширенное окно вокруг выбранного дня.

    Это помогает не потерять отправления из-за различий
    между локальным временем магазина и временем API.

    Важно:
    запрос всё равно остаётся узким — всего ±1 день.
    """

    start_date = selected_date - timedelta(days=1)
    end_date = selected_date + timedelta(days=2)

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

    return start.isoformat(), end.isoformat()


def safe_str(value):
    if value is None:
        return ""

    if isinstance(value, bool):
        return str(value)

    return str(value)


# ============================================================
# РАЗБОР ОТПРАВЛЕНИЯ
# ============================================================

def convert_posting(item: dict) -> dict:
    """
    Приводит ответ Ozon к единому виду.

    Главное поле:
        posting_number

    Дополнительно:
        status
        article
        product
        quantity
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


# ============================================================
# ИЗВЛЕЧЕНИЕ СПИСКА ИЗ ОТВЕТА API
# ============================================================

def extract_posting_items(data: dict) -> list:
    """
    Ozon может возвращать данные в разных вложенных структурах.
    Пробуем несколько вариантов, не меняя сам endpoint.
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
            "posting",
        ):
            value = result.get(key)

            if isinstance(value, list):
                return value

        # Иногда результат сам является одним объектом.
        if result.get("posting_number") or result.get("postingNumber"):
            return [result]

    for key in (
        "postings",
        "items",
        "orders",
        "posting",
    ):
        value = data.get(key)

        if isinstance(value, list):
            return value

    return []


# ============================================================
# ПОЛУЧЕНИЕ ОТПРАВЛЕНИЙ
# ============================================================

def get_postings(
    client_id: str,
    api_key: str,
    selected_date: date,
):
    """
    Получает отправления FBS через:

        /v1/assembly/fbs/posting/list

    Timestamp передаётся с timezone +03:00.

    Используем небольшое расширенное окно ±1 день,
    потому что cutoff у Ozon может быть привязан
    не строго к локальной календарной дате.

    Здесь специально НЕ фильтруем результат по неизвестному
    полю даты. Сначала получаем реальные posting_number.
    """

    headers = get_headers(client_id, api_key)

    cutoff_from, cutoff_to = get_expanded_ozon_range(selected_date)

    all_items = []
    cursor = ""

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
            f"Получение отправлений... страница {page_number}"
        )

        response = None

        for attempt in range(1, MAX_RETRIES + 1):

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
                        f"Ошибка соединения с Ozon API: {exc}"
                    )

                time.sleep(attempt * 2)
                continue

            if response.status_code == 200:
                break

            if response.status_code in (429, 500, 502, 503, 504):
                if attempt >= MAX_RETRIES:
                    raise RuntimeError(
                        f"Ozon API HTTP {response.status_code}\n\n"
                        f"{response.text}"
                    )

                time.sleep(attempt * 2)
                continue

            # Для остальных ошибок сразу показываем тело ответа.
            raise RuntimeError(
                f"Ozon API HTTP {response.status_code}\n\n"
                f"{response.text}"
            )

        if response is None:
            raise RuntimeError("Ozon API не вернул ответ.")

        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(
                "Ozon API вернул ответ, который не является JSON:\n\n"
                + response.text[:5000]
            )

        page_items = extract_posting_items(data)

        if not page_items:
            # Сохраняем raw ответ для диагностики.
            if not all_items:
                return [], data

            break

        all_items.extend(page_items)

        # Пытаемся найти cursor в разных местах.
        next_cursor = ""

        if isinstance(data.get("cursor"), str):
            next_cursor = data.get("cursor") or ""

        result = data.get("result")

        if isinstance(result, dict):
            if isinstance(result.get("cursor"), str):
                next_cursor = result.get("cursor") or next_cursor

        # Если курсора нет — последняя страница.
        if not next_cursor:
            break

        if next_cursor == cursor:
            break

        cursor = next_cursor

        progress.progress(
            min(0.95, 0.1 + (len(all_items) / 1000))
        )

        if len(page_items) < 1000:
            break

    progress.progress(1.0)
    status_box.success(
        f"Получено записей API: {len(all_items)}"
    )

    postings = []

    for item in all_items:

        if not isinstance(item, dict):
            continue

        posting = convert_posting(item)

        if posting["posting_number"]:
            postings.append(posting)

    # Убираем дубли по posting_number.
    unique = {}

    for posting in postings:
        number = posting["posting_number"]

        if number not in unique:
            unique[number] = posting

    postings = list(unique.values())

    return postings, data if isinstance(data, dict) else {}


# ============================================================
# FBS ИЗ ТЕКСТА PDF
# ============================================================

def extract_fbs_from_text(text: str):
    """
    Извлекает FBS-номер из текста одной этикетки.

    Пример текста Ozon:

    FBS: 1123559 0128758204-0424-1 8204 ...

    Нам нужен:

    0128758204-0424-1
    """

    if not text:
        return None

    patterns = [
        # Основной вариант.
        r"FBS\s*:\s*\d+\s+([0-9]{6,20}-[0-9]{2,8}-[0-9]+)",

        # Более свободный вариант.
        r"FBS\s*[:\-]?\s*(?:\d+\s+)?([0-9]{6,20}-[0-9]{2,8}-[0-9]+)",

        # Запасной вариант:
        # ищем саму структуру FBS-номера.
        r"\b([0-9]{6,20}-[0-9]{2,8}-[0-9]+)\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return match.group(1).strip()

    return None


def extract_fbs_from_page(page):
    """
    Читает текст страницы PDF и достаёт только FBS.

    Сырой текст страницы наружу НЕ показываем.
    """

    try:
        text = page.extract_text() or ""
    except Exception:
        text = ""

    return extract_fbs_from_text(text)


# ============================================================
# РАЗДЕЛЕНИЕ PDF ЭТИКЕТОК
# ============================================================

def split_label_pdf(pdf_bytes: bytes):
    """
    Возвращает список:

        {
            "fbs": "...",
            "page_bytes": b"...",
            "page_number": 1
        }

    Каждая страница обрабатывается отдельно.
    """

    reader = PdfReader(io.BytesIO(pdf_bytes))

    result = []

    for index, page in enumerate(reader.pages):

        writer = PdfWriter()
        writer.add_page(page)

        output = io.BytesIO()
        writer.write(output)

        page_bytes = output.getvalue()

        fbs = extract_fbs_from_page(page)

        result.append(
            {
                "fbs": fbs,
                "page_bytes": page_bytes,
                "page_number": index + 1,
            }
        )

    return result


# ============================================================
# ПОЛУЧЕНИЕ ЭТИКЕТОК
# ============================================================

def request_label_batch(
    client_id: str,
    api_key: str,
    posting_numbers: list,
):
    """
    Получает PDF этикеток максимум для 20 posting_number.
    """

    headers = get_headers(client_id, api_key)

    payload = {
        "posting_number": posting_numbers
    }

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

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
                f"Ошибка получения этикеток: {exc}"
            )

        if response.status_code == 200:

            content_type = (
                response.headers.get(
                    "Content-Type",
                    ""
                ).lower()
            )

            if (
                response.content[:4] == b"%PDF"
                or "application/pdf" in content_type
            ):
                return response.content

            # Иногда API может вернуть JSON с ошибкой
            # даже при неожиданном статусе.
            try:
                data = response.json()
            except Exception:
                data = response.text[:5000]

            last_error = (
                "Ozon не вернул PDF:\n"
                + str(data)
            )

        elif response.status_code in (
            429,
            500,
            502,
            503,
            504,
        ):

            last_error = (
                f"HTTP {response.status_code}: "
                f"{response.text[:2000]}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(attempt * 2)
                continue

        else:

            last_error = (
                f"HTTP {response.status_code}: "
                f"{response.text[:5000]}"
            )

            break

    raise RuntimeError(
        last_error or "Не удалось получить PDF этикеток."
    )


def get_labels(
    client_id: str,
    api_key: str,
    posting_numbers: list,
):
    """
    Получает все этикетки партиями по 20.

    ВАЖНО:
    маппинг выполняется НЕ по позиции страницы,
    а по FBS-номеру, извлечённому непосредственно
    из каждой страницы PDF.
    """

    all_labels = []
    errors = []

    total = len(posting_numbers)

    batches = [
        posting_numbers[i:i + LABEL_BATCH_SIZE]
        for i in range(0, total, LABEL_BATCH_SIZE)
    ]

    progress = st.progress(0)
    status_box = st.empty()

    for batch_index, batch in enumerate(batches, start=1):

        status_box.info(
            f"Получение этикеток: "
            f"партия {batch_index}/{len(batches)} "
            f"({len(batch)} отправлений)"
        )

        try:

            pdf_bytes = request_label_batch(
                client_id,
                api_key,
                batch,
            )

            pages = split_label_pdf(pdf_bytes)

            all_labels.extend(pages)

        except Exception as exc:

            # Если пакет целиком не получился,
            # пробуем по одной этикетке.
            for posting_number in batch:

                try:

                    pdf_bytes = request_label_batch(
                        client_id,
                        api_key,
                        [posting_number],
                    )

                    pages = split_label_pdf(pdf_bytes)

                    all_labels.extend(pages)

                except Exception as single_exc:

                    errors.append(
                        {
                            "posting_number": posting_number,
                            "error": str(single_exc),
                        }
                    )

        progress.progress(
            batch_index / len(batches)
        )

        # Небольшая пауза между пакетами.
        if batch_index < len(batches):
            time.sleep(0.5)

    progress.progress(1.0)

    status_box.success(
        f"Получено страниц этикеток: {len(all_labels)}"
    )

    return all_labels, errors


# ============================================================
# ШРИФТ
# ============================================================

def register_font():
    """
    Ищем DejaVu Sans.
    Нужен для нормального отображения кириллицы
    на информационных страницах.
    """

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/local/share/fonts/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ]

    for path in candidates:

        try:
            pdfmetrics.registerFont(
                TTFont("AppFont", path)
            )

            return "AppFont"

        except Exception:
            continue

    return "Helvetica"


APP_FONT = register_font()


# ============================================================
# ИНФОРМАЦИОННАЯ СТРАНИЦА
# ============================================================

def create_info_page(posting: dict):
    """
    Создаёт страницу с информацией об отправлении.
    """

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=A4,
    )

    width, height = A4

    left = 45

    y = height - 70

    c.setFont(APP_FONT, 20)
    c.drawString(
        left,
        y,
        "Информация об отправлении",
    )

    y -= 45

    c.setFont(APP_FONT, 12)

    rows = [
        ("Отправление", posting.get("posting_number", "")),
        ("Артикул", posting.get("article", "")),
        ("Товар", posting.get("product", "")),
        ("Количество", posting.get("quantity", "")),
        ("Статус", posting.get("status", "")),
    ]

    for title, value in rows:

        c.setFont(APP_FONT, 11)
        c.drawString(
            left,
            y,
            f"{title}:",
        )

        value = safe_str(value)

        # Простейшее ограничение длины,
        # чтобы длинный товар не вылезал за страницу.
        if len(value) > 95:
            value = value[:92] + "..."

        c.setFont(APP_FONT, 11)

        c.drawString(
            left + 115,
            y,
            value,
        )

        y -= 28

    c.save()

    buffer.seek(0)

    return buffer.getvalue()


# ============================================================
# СОЗДАНИЕ ИТОГОВОГО PDF
# ============================================================

def build_final_pdf(mapped_labels: list):
    """
    Для каждой этикетки:

        страница этикетки
        +
        информационная страница

    """

    writer = PdfWriter()

    for item in mapped_labels:

        label_pdf = PdfReader(
            io.BytesIO(
                item["page_bytes"]
            )
        )

        for page in label_pdf.pages:
            writer.add_page(page)

        info_bytes = create_info_page(
            item["posting"]
        )

        info_pdf = PdfReader(
            io.BytesIO(info_bytes)
        )

        for page in info_pdf.pages:
            writer.add_page(page)

    output = io.BytesIO()

    writer.write(output)

    return output.getvalue()


# ============================================================
# ОСНОВНОЙ ЗАПУСК
# ============================================================

if st.button(
    "🚀 Получить отправления и этикетки",
    type="primary",
    use_container_width=True,
):

    if not client_id.strip():
        st.error("Введите Client-Id.")
        st.stop()

    if not api_key.strip():
        st.error("Введите Api-Key.")
        st.stop()

    # --------------------------------------------------------
    # 1. ПОЛУЧАЕМ ОТПРАВЛЕНИЯ
    # --------------------------------------------------------

    st.subheader("1️⃣ Получение отправлений")

    try:

        postings, raw_response = get_postings(
            client_id.strip(),
            api_key.strip(),
            selected_date,
        )

    except Exception as exc:

        st.error(
            f"Ошибка получения отправлений:\n\n{exc}"
        )

        st.stop()

    st.write(
        f"**Найдено уникальных отправлений: "
        f"{len(postings)}**"
    )

    if not postings:

        st.error(
            "За выбранный период Ozon не вернул отправления."
        )

        # Показываем только технический JSON,
        # если API что-то реально вернул.
        if raw_response:

            with st.expander(
                "🔧 Технический ответ Ozon API"
            ):

                st.json(raw_response)

        st.stop()

    # --------------------------------------------------------
    # 2. СТРОИМ MAP POSTING_NUMBER -> POSTING
    # --------------------------------------------------------

    postings_map = {
        p["posting_number"]: p
        for p in postings
        if p["posting_number"]
    }

    st.info(
        f"В API доступно {len(postings_map)} "
        f"уникальных posting_number."
    )

    # --------------------------------------------------------
    # 3. ПОЛУЧАЕМ ЭТИКЕТКИ
    # --------------------------------------------------------

    st.subheader("2️⃣ Получение этикеток")

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
            f"Ошибка получения этикеток:\n\n{exc}"
        )

        st.stop()

    if not labels:

        st.error(
            "Ozon не вернул ни одной страницы этикеток."
        )

        if label_errors:

            st.dataframe(
                label_errors,
                use_container_width=True,
                hide_index=True,
            )

        st.stop()

    # --------------------------------------------------------
    # 4. МАППИНГ ПО FBS
    # --------------------------------------------------------

    st.subheader("3️⃣ Маппинг этикеток")

    mapped_labels = []
    unknown_labels = []
    duplicate_labels = []

    seen_fbs = set()

    for label in labels:

        fbs = label.get("fbs")

        if not fbs:

            unknown_labels.append(
                {
                    "Страница": label["page_number"],
                    "Причина": "FBS-номер не распознан",
                }
            )

            continue

        if fbs in seen_fbs:

            duplicate_labels.append(
                {
                    "FBS": fbs,
                    "Страница": label["page_number"],
                }
            )

            continue

        seen_fbs.add(fbs)

        posting = postings_map.get(fbs)

        if not posting:

            unknown_labels.append(
                {
                    "FBS": fbs,
                    "Страница": label["page_number"],
                    "Причина": "FBS отсутствует среди полученных отправлений",
                }
            )

            continue

        mapped_labels.append(
            {
                "fbs": fbs,
                "page_bytes": label["page_bytes"],
                "page_number": label["page_number"],
                "posting": posting,
            }
        )

    # --------------------------------------------------------
    # 5. СТАТИСТИКА
    # --------------------------------------------------------

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
            "Успешный маппинг",
            len(mapped_labels),
        )

    with col4:
        st.metric(
            "Не сопоставлено",
            len(unknown_labels),
        )

    # --------------------------------------------------------
    # 6. ОСНОВНАЯ ТАБЛИЦА
    # --------------------------------------------------------

    if mapped_labels:

        table_rows = []

        for item in mapped_labels:

            posting = item["posting"]

            table_rows.append(
                {
                    "Отправление": posting["posting_number"],
                    "Артикул": posting["article"],
                    "Товар": posting["product"],
                    "Кол-во": posting["quantity"],
                    "Статус": posting["status"],
                    "Этикетка": "Да",
                    "Страница": item["page_number"],
                }
            )

        st.dataframe(
            table_rows,
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.warning(
            "Не удалось сопоставить ни одной этикетки."
        )

    # --------------------------------------------------------
    # 7. НЕСОПОСТАВЛЕННЫЕ
    # --------------------------------------------------------

    if unknown_labels:

        with st.expander(
            f"⚠️ Несопоставленные этикетки: "
            f"{len(unknown_labels)}"
        ):

            st.dataframe(
                unknown_labels,
                use_container_width=True,
                hide_index=True,
            )

    # --------------------------------------------------------
    # 8. ОШИБКИ ПОЛУЧЕНИЯ
    # --------------------------------------------------------

    if label_errors:

        with st.expander(
            f"⚠️ Ошибки получения этикеток: "
            f"{len(label_errors)}"
        ):

            st.dataframe(
                label_errors,
                use_container_width=True,
                hide_index=True,
            )

    # --------------------------------------------------------
    # 9. ДУБЛИКАТЫ
    # --------------------------------------------------------

    if duplicate_labels:

        with st.expander(
            f"⚠️ Дубли страниц: "
            f"{len(duplicate_labels)}"
        ):

            st.dataframe(
                duplicate_labels,
                use_container_width=True,
                hide_index=True,
            )

    # --------------------------------------------------------
    # 10. ИТОГОВЫЙ PDF
    # --------------------------------------------------------

    if mapped_labels:

        st.subheader("4️⃣ Итоговый PDF")

        with st.spinner(
            "Формируем итоговый PDF..."
        ):

            final_pdf = build_final_pdf(
                mapped_labels
            )

        st.success(
            f"Готово. В итоговом PDF "
            f"{len(mapped_labels) * 2} страниц."
        )

        filename = (
            f"ozon_fbs_"
            f"{selected_date.strftime('%Y-%m-%d')}.pdf"
        )

        st.download_button(
            label="📥 Скачать итоговый PDF",
            data=final_pdf,
            file_name=filename,
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )

    # --------------------------------------------------------
    # 11. ДИАГНОСТИКА
    # --------------------------------------------------------

    with st.expander("🔧 Диагностика"):

        st.write(
            f"Дата выбрана: "
            f"{selected_date.strftime('%Y-%m-%d')}"
        )

        cutoff_from, cutoff_to = get_expanded_ozon_range(
            selected_date
        )

        st.write(
            f"cutoff_from: `{cutoff_from}`"
        )

        st.write(
            f"cutoff_to: `{cutoff_to}`"
        )

        st.write(
            f"Отправлений API: {len(postings_map)}"
        )

        st.write(
            f"Этикеток: {len(labels)}"
        )

        st.write(
            f"Распознано FBS: "
            f"{sum(1 for x in labels if x.get('fbs'))}"
        )

        st.write(
            f"Сопоставлено: {len(mapped_labels)}"
        )

        st.write(
            f"Не сопоставлено: {len(unknown_labels)}"
        )

