import streamlit as st
import requests
import re
import io
from datetime import datetime, timedelta

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# НАСТРОЙКА
# ============================================================

st.set_page_config(
    page_title="Ozon FBS — Этикетки + API",
    page_icon="🖨️",
    layout="wide"
)


# ============================================================
# ШРИФТ
# ============================================================

def register_font():
    """
    Пытаемся найти Roboto.
    Если не найден — ReportLab будет использовать Helvetica.
    """

    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]

    regular = None
    bold = None

    for path in font_paths:
        if "Bold" not in path and regular is None:
            try:
                pdfmetrics.registerFont(TTFont("AppFont", path))
                regular = "AppFont"
            except Exception:
                pass

        if "Bold" in path and bold is None:
            try:
                pdfmetrics.registerFont(TTFont("AppFontBold", path))
                bold = "AppFontBold"
            except Exception:
                pass

    return regular or "Helvetica", bold or "Helvetica-Bold"


FONT_REGULAR, FONT_BOLD = register_font()


# ============================================================
# НОРМАЛИЗАЦИЯ НОМЕРА ОТПРАВЛЕНИЯ
# ============================================================

def normalize_shipment(value):
    """
    Нормализация номера отправления.
    """

    if value is None:
        return ""

    value = str(value).strip().lower()

    value = value.replace(" ", "")
    value = value.replace("\n", "")
    value = value.replace("\r", "")
    value = value.replace("—", "-")
    value = value.replace("–", "-")

    return value


def compact_shipment(value):
    """
    Максимально компактная форма номера.
    """

    value = normalize_shipment(value)

    return re.sub(
        r"[^0-9a-zа-яё]",
        "",
        value
    )


# ============================================================
# ПОИСК НОМЕРА ОТПРАВЛЕНИЯ НА ЭТИКЕТКЕ
# ============================================================

def extract_shipment_from_label(page):
    """
    Извлекает номер отправления непосредственно из PDF этикетки.
    """

    try:
        text = page.extract_text() or ""
    except Exception:
        text = ""

    if not text:
        return None

    # Основной формат Ozon:
    # 12345678-0000-1
    patterns = [
        r"\b\d{8,15}-\d{3,6}-\d+\b",
        r"\b\d{8,15}-\d{4}-\d+\b",
        r"\b\d{8,15}-\d{4}\b",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text)

        if matches:
            # Берём самый длинный / наиболее похожий
            matches = sorted(
                matches,
                key=len,
                reverse=True
            )

            return normalize_shipment(matches[0])

    # Дополнительный вариант:
    # если номер находится рядом со словами отправление / заказ
    lines = [
        x.strip()
        for x in text.splitlines()
        if x.strip()
    ]

    for line in lines:

        if (
            "отправлен" in line.lower()
            or "posting" in line.lower()
            or "заказ" in line.lower()
        ):

            matches = re.findall(
                r"\d{8,15}(?:-\d+)+",
                line
            )

            if matches:
                return normalize_shipment(matches[0])

    return None


# ============================================================
# API Ozon
# ============================================================

API_URL = "https://api-seller.ozon.ru/v1/assembly/fbs/posting/list"


def api_request(
    client_id,
    api_key,
    date_from,
    date_to,
    limit=1000
):
    """
    Один запрос API с пагинацией.
    """

    headers = {
        "Client-Id": str(client_id).strip(),
        "Api-Key": str(api_key).strip(),
        "Content-Type": "application/json"
    }

    all_postings = []

    cursor = ""

    page_number = 0

    while True:

        page_number += 1

        payload = {
            "filter": {
                "cutoff_from": date_from,
                "cutoff_to": date_to
            },
            "limit": limit,
            "sort_dir": "ASC"
        }

        if cursor:
            payload["cursor"] = cursor

        try:

            response = requests.post(
                API_URL,
                headers=headers,
                json=payload,
                timeout=60
            )

        except Exception as e:
            raise RuntimeError(
                f"Ошибка соединения с Ozon API: {e}"
            )

        if response.status_code != 200:

            try:
                error_text = response.json()
            except Exception:
                error_text = response.text

            raise RuntimeError(
                f"Ozon API HTTP {response.status_code}: "
                f"{error_text}"
            )

        try:
            data = response.json()
        except Exception:
            raise RuntimeError(
                "Ozon API вернул некорректный JSON."
            )

        postings = data.get("postings", [])

        if not postings:
            break

        all_postings.extend(postings)

        next_cursor = data.get("cursor")

        # Если курсора нет — данных больше нет
        if not next_cursor:
            break

        # Защита от зацикливания
        if next_cursor == cursor:
            break

        cursor = next_cursor

        # Если вернулось меньше limit,
        # обычно следующей страницы уже нет.
        if len(postings) < limit:
            break

    return all_postings


# ============================================================
# ПРЕОБРАЗОВАНИЕ POSTING В НАШУ СТРУКТУРУ
# ============================================================

def posting_to_record(posting):
    """
    Превращает ответ API Ozon в удобную структуру.
    """

    posting_number = (
        posting.get("posting_number")
        or posting.get("order_number")
        or posting.get("postingNumber")
        or ""
    )

    products = (
        posting.get("products")
        or posting.get("items")
        or []
    )

    names = []
    articles = []
    quantities = []

    for product in products:

        if not isinstance(product, dict):
            continue

        name = (
            product.get("name")
            or product.get("product_name")
            or ""
        )

        article = (
            product.get("offer_id")
            or product.get("offerId")
            or product.get("sku")
            or product.get("article")
            or ""
        )

        quantity = (
            product.get("quantity")
            or product.get("qty")
            or 0
        )

        if name:
            names.append(str(name))

        if article:
            articles.append(str(article))

        try:
            quantities.append(float(quantity))
        except Exception:
            pass

    total_qty = sum(quantities)

    if total_qty.is_integer():
        total_qty = int(total_qty)

    return {
        "shipment": str(posting_number),
        "article": " + ".join(articles) if articles else "-",
        "name": " + ".join(names) if names else "-",
        "qty": str(total_qty),
        "raw": posting
    }


# ============================================================
# ДОБАВЛЕНИЕ API ДАННЫХ В MAP
# ============================================================

def add_postings_to_mapping(postings, api_mapping):
    """
    Добавляет postings в общий словарь.
    """

    added = 0

    for posting in postings:

        record = posting_to_record(posting)

        shipment = record["shipment"]

        if not shipment:
            continue

        normalized = normalize_shipment(shipment)

        if not normalized:
            continue

        # Основной ключ
        api_mapping[normalized] = record

        # Компактный ключ
        compact = compact_shipment(normalized)

        if compact:
            api_mapping[f"__compact__{compact}"] = record

        added += 1

    return added


# ============================================================
# ПОЛУЧЕНИЕ API ДАННЫХ КУСКАМИ
# ============================================================

def get_assembly_data_chunked(
    client_id,
    api_key,
    days_back=180,
    days_forward=7,
    chunk_days=30
):
    """
    Получаем API кусками.

    Например:

    180 дней назад
    ↓
    150
    ↓
    120
    ↓
    ...
    ↓
    сегодня
    ↓
    +7 дней

    Это позволяет не зависеть от одного огромного периода.
    """

    api_mapping = {}

    today = datetime.now()

    start_date = today - timedelta(days=days_back)
    end_date = today + timedelta(days=days_forward)

    total_requests = 0
    total_postings = 0

    current = start_date

    progress = st.progress(
        0,
        text="Получение данных Ozon API..."
    )

    total_seconds = max(
        (end_date - start_date).total_seconds(),
        1
    )

    while current < end_date:

        chunk_end = min(
            current + timedelta(days=chunk_days),
            end_date
        )

        date_from = current.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        date_to = chunk_end.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        total_requests += 1

        postings = api_request(
            client_id=client_id,
            api_key=api_key,
            date_from=date_from,
            date_to=date_to
        )

        total_postings += len(postings)

        add_postings_to_mapping(
            postings,
            api_mapping
        )

        elapsed = (
            chunk_end - start_date
        ).total_seconds()

        percent = min(
            elapsed / total_seconds,
            1
        )

        progress.progress(
            percent,
            text=(
                f"API: {date_from[:10]} → "
                f"{date_to[:10]} | "
                f"получено {len(postings)}"
            )
        )

        current = chunk_end

    progress.progress(
        1,
        text=(
            f"API завершено. "
            f"Уникальных отправлений: "
            f"{len([k for k in api_mapping if not k.startswith('__compact__')])}"
        )
    )

    return api_mapping, total_requests, total_postings


# ============================================================
# ПОИСК В API
# ============================================================

def find_api_posting(shipment, api_mapping):

    if not shipment:
        return None

    normalized = normalize_shipment(shipment)

    # 1. Точное совпадение
    if normalized in api_mapping:
        return api_mapping[normalized]

    # 2. Компактное совпадение
    compact = compact_shipment(normalized)

    if compact:

        compact_key = f"__compact__{compact}"

        if compact_key in api_mapping:
            return api_mapping[compact_key]

    return None


# ============================================================
# ПЕРЕНОС ТЕКСТА
# ============================================================

def split_text(text, chars=45):

    if not text:
        return ["-"]

    text = str(text)

    result = []

    while len(text) > chars:

        pos = text.rfind(
            " ",
            0,
            chars
        )

        if pos <= 0:
            pos = chars

        result.append(
            text[:pos]
        )

        text = text[pos:].strip()

    if text:
        result.append(text)

    return result


# ============================================================
# ИНФОРМАЦИОННАЯ СТРАНИЦА
# ============================================================

def create_info_label(
    width,
    height,
    order_number,
    product_info,
    match_status="OK"
):
    """
    Создаёт страницу с информацией после этикетки.
    """

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=(width, height)
    )

    # Заголовок
    if match_status == "OK":
        title = "ИНФОРМАЦИЯ ОТПРАВЛЕНИЯ"
    else:
        title = "ОТПРАВЛЕНИЕ НЕ НАЙДЕНО В API"

    c.setFont(
        FONT_BOLD,
        min(18, width / 22)
    )

    c.drawCentredString(
        width / 2,
        height - 40,
        title
    )

    y = height - 85

    # Отправление
    c.setFont(
        FONT_BOLD,
        11
    )

    c.drawString(
        30,
        y,
        "Отправление:"
    )

    c.setFont(
        FONT_REGULAR,
        11
    )

    c.drawString(
        130,
        y,
        str(order_number or "—")
    )

    y -= 30

    # Статус
    c.setFont(
        FONT_BOLD,
        11
    )

    c.drawString(
        30,
        y,
        "Статус:"
    )

    c.setFont(
        FONT_REGULAR,
        11
    )

    if match_status == "OK":
        status_text = "Найдено в Ozon API"
    else:
        status_text = "Не найдено в Ozon API"

    c.drawString(
        130,
        y,
        status_text
    )

    y -= 40

    # Артикул
    c.setFont(
        FONT_BOLD,
        11
    )

    c.drawString(
        30,
        y,
        "Артикул:"
    )

    y -= 18

    c.setFont(
        FONT_REGULAR,
        10
    )

    for line in split_text(
        product_info.get("article", "-"),
        55
    ):
        c.drawString(
            30,
            y,
            line
        )
        y -= 15

    y -= 10

    # Товар
    c.setFont(
        FONT_BOLD,
        11
    )

    c.drawString(
        30,
        y,
        "Товар:"
    )

    y -= 18

    c.setFont(
        FONT_REGULAR,
        10
    )

    for line in split_text(
        product_info.get("name", "-"),
        55
    ):
        c.drawString(
            30,
            y,
            line
        )
        y -= 15

    y -= 10

    # Количество
    c.setFont(
        FONT_BOLD,
        11
    )

    c.drawString(
        30,
        y,
        "Количество:"
    )

    c.setFont(
        FONT_REGULAR,
        11
    )

    c.drawString(
        130,
        y,
        str(product_info.get("qty", "-"))
    )

    if match_status != "OK":

        y -= 45

        c.setFont(
            FONT_BOLD,
            10
        )

        warning = [
            "Этикетка сохранена в итоговом PDF.",
            "Данные товара не получены из API.",
            "Проверьте номер отправления."
        ]

        for line in warning:

            c.drawString(
                30,
                y,
                line
            )

            y -= 15

    c.showPage()
    c.save()

    buffer.seek(0)

    return buffer


# ============================================================
# СОЗДАНИЕ ИТОГОВОГО PDF
# ============================================================

def create_final_pdf(
    uploaded_file,
    api_mapping,
    label_records
):

    reader = PdfReader(
        uploaded_file
    )

    writer = PdfWriter()

    found = 0
    not_found = 0

    diagnostics = []

    for record in label_records:

        page_number = record["page"]

        page = reader.pages[
            page_number - 1
        ]

        shipment = record["shipment"]

        api_record = find_api_posting(
            shipment,
            api_mapping
        )

        if api_record:

            found += 1

            info = api_record

            status = "OK"

        else:

            not_found += 1

            info = {
                "article": "-",
                "name": "-",
                "qty": "-"
            }

            status = "НЕ НАЙДЕНО"

        # Сначала оригинальная этикетка
        writer.add_page(page)

        # Потом наша информационная страница
        width = float(
            page.mediabox.width
        )

        height = float(
            page.mediabox.height
        )

        info_pdf = create_info_label(
            width,
            height,
            shipment or "—",
            info,
            status
        )

        info_reader = PdfReader(
            info_pdf
        )

        writer.add_page(
            info_reader.pages[0]
        )

        diagnostics.append({
            "Страница": page_number,
            "Отправление": shipment or "—",
            "Артикул": (
                info.get("article", "-")
                if api_record
                else "-"
            ),
            "Товар": (
                info.get("name", "-")
                if api_record
                else "-"
            ),
            "Кол-во": (
                info.get("qty", "-")
                if api_record
                else "-"
            ),
            "API": status
        })

    output = io.BytesIO()

    writer.write(output)

    output.seek(0)

    return (
        output,
        diagnostics,
        found,
        not_found
    )


# ============================================================
# ИНТЕРФЕЙС
# ============================================================

st.title(
    "🖨️ Ozon FBS — Этикетки + API"
)

st.write(
    "Загрузите PDF с этикетками Ozon. "
    "Приложение само найдёт номера отправлений, "
    "получит данные через API и добавит "
    "информационную страницу после каждой этикетки."
)


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header(
    "🔑 Ozon API"
)

client_id = st.sidebar.text_input(
    "Client-Id",
    type="password"
)

api_key = st.sidebar.text_input(
    "Api-Key",
    type="password"
)

st.sidebar.markdown(
    "---"
)

st.sidebar.subheader(
    "📅 Период поиска"
)

days_back = st.sidebar.number_input(
    "Дней назад",
    min_value=30,
    max_value=365,
    value=180,
    step=30
)

days_forward = st.sidebar.number_input(
    "Дней вперёд",
    min_value=0,
    max_value=60,
    value=7,
    step=7
)

st.sidebar.info(
    "API будет запрашиваться кусками "
    "по 30 дней. Это позволяет найти "
    "отправления, которые не попали "
    "в один большой запрос."
)


# ============================================================
# ЗАГРУЗКА PDF
# ============================================================

uploaded_file = st.file_uploader(
    "📄 PDF с этикетками",
    type=["pdf"]
)


# ============================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================

if uploaded_file:

    # Читаем PDF
    try:

        reader = PdfReader(
            uploaded_file
        )

    except Exception as e:

        st.error(
            f"Не удалось открыть PDF: {e}"
        )

        st.stop()

    total_pages = len(
        reader.pages
    )

    st.info(
        f"📄 Страниц в PDF: **{total_pages}**"
    )

    # --------------------------------------------------------
    # 1. РАСПОЗНАЁМ ВСЕ ЭТИКЕТКИ
    # --------------------------------------------------------

    st.subheader(
        "1️⃣ Распознавание этикеток"
    )

    label_records = []

    progress_labels = st.progress(
        0,
        text="Читаем номера отправлений..."
    )

    for i, page in enumerate(
        reader.pages,
        start=1
    ):

        shipment = extract_shipment_from_label(
            page
        )

        label_records.append({
            "page": i,
            "shipment": shipment
        })

        progress_labels.progress(
            i / total_pages,
            text=(
                f"Этикетка {i} из "
                f"{total_pages}"
            )
        )

    recognized = sum(
        1
        for x in label_records
        if x["shipment"]
    )

    not_recognized = (
        total_pages - recognized
    )

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "Этикеток",
        total_pages
    )

    col2.metric(
        "Номер распознан",
        recognized
    )

    col3.metric(
        "Не распознан",
        not_recognized
    )

    # --------------------------------------------------------
    # ЕСЛИ API НЕ УКАЗАН
    # --------------------------------------------------------

    if not client_id or not api_key:

        st.warning(
            "Введите Client-Id и Api-Key в боковой панели."
        )

        st.stop()

    # --------------------------------------------------------
    # КНОПКА
    # --------------------------------------------------------

    st.subheader(
        "2️⃣ Получение данных Ozon"
    )

    start_button = st.button(
        "🚀 Получить API и собрать PDF",
        type="primary",
        use_container_width=True
    )

    if start_button:

        try:

            # ------------------------------------------------
            # API
            # ------------------------------------------------

            api_mapping, total_requests, total_postings = (
                get_assembly_data_chunked(
                    client_id=client_id,
                    api_key=api_key,
                    days_back=int(days_back),
                    days_forward=int(days_forward),
                    chunk_days=30
                )
            )

            unique_api = len([
                x
                for x in api_mapping
                if not x.startswith(
                    "__compact__"
                )
            ])

            st.success(
                f"Получено из API: "
                f"**{total_postings}** записей. "
                f"Уникальных отправлений: "
                f"**{unique_api}**. "
                f"Запросов: **{total_requests}**."
            )

            # ------------------------------------------------
            # ПРЕДВАРИТЕЛЬНОЕ СОПОСТАВЛЕНИЕ
            # ------------------------------------------------

            preliminary_found = 0
            preliminary_missing = []

            for record in label_records:

                shipment = record["shipment"]

                if not shipment:
                    continue

                api_record = find_api_posting(
                    shipment,
                    api_mapping
                )

                if api_record:
                    preliminary_found += 1

                else:
                    preliminary_missing.append(
                        shipment
                    )

            st.subheader(
                "3️⃣ Результат сопоставления"
            )

            col1, col2, col3 = st.columns(3)

            col1.metric(
                "Этикеток",
                total_pages
            )

            col2.metric(
                "Найдено в API",
                preliminary_found
            )

            col3.metric(
                "Нет в API",
                len(preliminary_missing)
            )

            # ------------------------------------------------
            # СОЗДАЁМ PDF
            # ------------------------------------------------

            final_pdf, diagnostics, found, not_found = (
                create_final_pdf(
                    uploaded_file,
                    api_mapping,
                    label_records
                )
            )

            # ------------------------------------------------
            # ИТОГ
            # ------------------------------------------------

            st.subheader(
                "4️⃣ Итог"
            )

            c1, c2, c3 = st.columns(3)

            c1.metric(
                "Всего этикеток",
                total_pages
            )

            c2.metric(
                "Найдено",
                found
            )

            c3.metric(
                "Не найдено",
                not_found
            )

            # ------------------------------------------------
            # СПИСОК НЕ НАЙДЕННЫХ
            # ------------------------------------------------

            missing_rows = [
                x
                for x in diagnostics
                if x["API"] != "OK"
            ]

            if missing_rows:

                st.warning(
                    f"Осталось не найдено: "
                    f"**{len(missing_rows)}**"
                )

                st.write(
                    "Эти номера были распознаны "
                    "на этикетках, но не нашлись "
                    "в результате запросов Ozon API:"
                )

                st.dataframe(
                    missing_rows,
                    use_container_width=True,
                    hide_index=True
                )

                # Текстовый список для копирования
                missing_numbers = "\n".join(
                    str(x["Отправление"])
                    for x in missing_rows
                )

                st.text_area(
                    "Номера для проверки",
                    value=missing_numbers,
                    height=200
                )

            else:

                st.success(
                    "🎉 Все этикетки успешно "
                    "сопоставлены с Ozon API!"
                )

            # ------------------------------------------------
            # ПОЛНАЯ ТАБЛИЦА
            # ------------------------------------------------

            with st.expander(
                "📋 Показать все сопоставления"
            ):

                st.dataframe(
                    diagnostics,
                    use_container_width=True,
                    hide_index=True
                )

            # ------------------------------------------------
            # СКАЧИВАНИЕ
            # ------------------------------------------------

            st.download_button(
                label="⬇️ Скачать Ready_Labels.pdf",
                data=final_pdf.getvalue(),
                file_name="Ready_Labels.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True
            )

        except Exception as e:

            st.error(
                "❌ Ошибка при обработке:"
            )

            st.exception(e)
