import io
import re
import time
import requests
import streamlit as st
import pandas as pd

from datetime import datetime, timedelta

from pypdf import PdfReader, PdfWriter

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# НАСТРОЙКИ
# ============================================================

POSTINGS_URL = (
    "https://api-seller.ozon.ru/v1/assembly/fbs/posting/list"
)

LABEL_URL = (
    "https://api-seller.ozon.ru/v2/posting/fbs/package-label"
)

# Ozon позволяет запрашивать этикетки пачками
LABEL_BATCH_SIZE = 20

# Повторная попытка получения этикетки
LABEL_RETRY_COUNT = 3
LABEL_RETRY_DELAY = 3


# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(
    page_title="Ozon FBS — Этикетки",
    page_icon="📦",
    layout="wide"
)

st.title("📦 Ozon FBS — этикетки для сборки")

st.caption(
    "Отправления → этикетки → точный маппинг по FBS"
)


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header("🔐 Ozon API")

client_id = st.sidebar.text_input(
    "Client-Id",
    type="password"
)

api_key = st.sidebar.text_input(
    "Api-Key",
    type="password"
)

st.sidebar.divider()

selected_date = st.sidebar.date_input(
    "Дата сборки",
    value=datetime.now().date()
)

st.sidebar.caption(
    "Обычно выбирайте сегодняшний день."
)


# ============================================================
# ПРОВЕРКА API
# ============================================================

if not client_id or not api_key:

    st.info(
        "Введите Client-Id и Api-Key в боковой панели."
    )

    st.stop()


# ============================================================
# HEADERS
# ============================================================

def get_headers():

    return {
        "Client-Id": str(client_id).strip(),
        "Api-Key": str(api_key).strip(),
        "Content-Type": "application/json"
    }


# ============================================================
# НОРМАЛИЗАЦИЯ FBS
# ============================================================

def normalize_fbs(value):

    if value is None:
        return ""

    value = str(value).strip()

    # Убираем пробелы вокруг
    value = re.sub(
        r"\s+",
        "",
        value
    )

    return value


# ============================================================
# ПОЛУЧЕНИЕ ОТПРАВЛЕНИЙ
# ============================================================

def get_postings(date_value):

    """
    Получает отправления через:

    POST /v1/assembly/fbs/posting/list

    ВАЖНО:

    Не пытаемся после ответа фильтровать postings
    по неизвестному полю даты.

    Запрашиваем расширенное окно ±1 день.
    Это защищает от проблем с часовым поясом
    и cutoff.
    """

    # --------------------------------------------------------
    # Расширенное окно
    # --------------------------------------------------------

    request_from = (
        date_value - timedelta(days=1)
    )

    request_to = (
        date_value + timedelta(days=1)
    )

    cutoff_from = (
        datetime.combine(
            request_from,
            datetime.min.time()
        ).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    )

    cutoff_to = (
        datetime.combine(
            request_to,
            datetime.max.time()
        ).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    )

    headers = get_headers()

    all_postings = []

    cursor = ""

    progress = st.progress(0)

    status_box = st.empty()

    page = 0

    while True:

        page += 1

        payload = {
            "filter": {
                "cutoff_from": cutoff_from,
                "cutoff_to": cutoff_to
            },
            "limit": 1000,
            "sort_dir": "ASC"
        }

        if cursor:
            payload["cursor"] = cursor

        status_box.write(
            f"📥 Получение отправлений — "
            f"страница {page}"
        )

        try:

            response = requests.post(
                POSTINGS_URL,
                headers=headers,
                json=payload,
                timeout=90
            )

        except Exception as e:

            progress.empty()
            status_box.empty()

            raise Exception(
                "Ошибка соединения с Ozon API:\n\n"
                + str(e)
            )

        # ----------------------------------------------------
        # HTTP ERROR
        # ----------------------------------------------------

        if response.status_code != 200:

            progress.empty()
            status_box.empty()

            raise Exception(
                f"Ozon API HTTP "
                f"{response.status_code}\n\n"
                f"{response.text[:5000]}"
            )

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------

        try:

            data = response.json()

        except Exception:

            progress.empty()
            status_box.empty()

            raise Exception(
                "Ozon вернул не JSON:\n\n"
                + response.text[:5000]
            )

        # ----------------------------------------------------
        # ИЗВЛЕКАЕМ RESULT
        # ----------------------------------------------------

        result = data.get(
            "result",
            {}
        )

        postings = []
        next_cursor = ""

        # ----------------------------------------------------
        # result = dict
        # ----------------------------------------------------

        if isinstance(result, dict):

            postings = (
                result.get("postings")
                or result.get("items")
                or result.get("orders")
                or []
            )

            next_cursor = (
                result.get("cursor")
                or ""
            )

        # ----------------------------------------------------
        # result = list
        # ----------------------------------------------------

        elif isinstance(result, list):

            postings = result

        # ----------------------------------------------------
        # Дополнительная защита:
        # иногда данные могут лежать непосредственно
        # на верхнем уровне
        # ----------------------------------------------------

        if not postings:

            postings = (
                data.get("postings")
                or data.get("items")
                or data.get("orders")
                or []
            )

        if not next_cursor:

            next_cursor = (
                data.get("cursor")
                or ""
            )

        # ----------------------------------------------------
        # Добавляем
        # ----------------------------------------------------

        if isinstance(postings, list):

            all_postings.extend(
                postings
            )

        status_box.write(
            f"📥 Получено отправлений: "
            f"{len(all_postings)}"
        )

        # ----------------------------------------------------
        # Pagination
        # ----------------------------------------------------

        if not next_cursor:
            break

        if next_cursor == cursor:
            break

        cursor = next_cursor

        progress.progress(
            min(
                0.95,
                0.1 + page * 0.05
            )
        )

        # Защита
        if page >= 100:
            break

    progress.progress(1)

    progress.empty()
    status_box.empty()

    return all_postings


# ============================================================
# ПОЛУЧЕНИЕ ЗНАЧЕНИЯ ИЗ СЛОВАРЯ
# ============================================================

def first_value(data, keys, default=""):

    if not isinstance(data, dict):
        return default

    for key in keys:

        value = data.get(key)

        if value is not None and value != "":

            return value

    return default


# ============================================================
# ПРЕОБРАЗОВАНИЕ POSTING
# ============================================================

def convert_posting(posting):

    """
    Приводим ответ Ozon к:

    posting_number
    article
    product
    quantity
    status
    """

    if not isinstance(posting, dict):
        return []

    posting_number = first_value(
        posting,
        [
            "posting_number",
            "postingNumber",
            "number"
        ]
    )

    status = first_value(
        posting,
        [
            "status",
            "status_name",
            "statusName"
        ]
    )

    products = (
        posting.get("products")
        or []
    )

    if not isinstance(products, list):

        products = []

    rows = []

    # ========================================================
    # Есть products
    # ========================================================

    if products:

        for product in products:

            if not isinstance(product, dict):
                continue

            article = first_value(
                product,
                [
                    "offer_id",
                    "offerId",
                    "sku"
                ]
            )

            product_name = first_value(
                product,
                [
                    "name",
                    "product_name",
                    "productName"
                ]
            )

            quantity = first_value(
                product,
                [
                    "quantity",
                    "qty"
                ],
                1
            )

            rows.append({

                "posting_number":
                    str(posting_number),

                "article":
                    str(article),

                "product":
                    str(product_name),

                "quantity":
                    quantity,

                "status":
                    str(status)
            })

    # ========================================================
    # Если products нет
    # ========================================================

    else:

        article = first_value(
            posting,
            [
                "offer_id",
                "offerId",
                "sku"
            ]
        )

        product_name = first_value(
            posting,
            [
                "name",
                "product_name",
                "productName"
            ]
        )

        quantity = first_value(
            posting,
            [
                "quantity",
                "qty"
            ],
            1
        )

        rows.append({

            "posting_number":
                str(posting_number),

            "article":
                str(article),

            "product":
                str(product_name),

            "quantity":
                quantity,

            "status":
                str(status)
        })

    return rows


# ============================================================
# НОРМАЛИЗАЦИЯ ОТПРАВЛЕНИЙ
# ============================================================

def normalize_postings(raw_postings):

    rows = []

    for posting in raw_postings:

        rows.extend(
            convert_posting(posting)
        )

    return rows


# ============================================================
# ИЗВЛЕЧЕНИЕ FBS ИЗ ЭТИКЕТКИ
# ============================================================

def extract_fbs_from_page(page):

    """
    Из этикетки ищем:

        FBS: 1123559 0128758204-0424-1

    Возвращаем:

        0128758204-0424-1

    Никакой текст этикетки пользователю
    не показываем.
    """

    try:

        text = page.extract_text() or ""

    except Exception:

        return ""

    if not text:
        return ""

    # --------------------------------------------------------
    # Нормализуем переносы
    # --------------------------------------------------------

    text = text.replace(
        "\r",
        "\n"
    )

    # --------------------------------------------------------
    # Основной вариант
    #
    # FBS: 1123559 0128758204-0424-1
    # --------------------------------------------------------

    patterns = [

        r"FBS\s*:\s*\d+\s+"
        r"([0-9]{6,20}-[0-9]{2,8}-[0-9]+)",

        r"FBS\s*[:\-]?\s*"
        r"(?:\d+\s+)?"
        r"([0-9]{6,20}-[0-9]{2,8}-[0-9]+)",

        r"\b"
        r"([0-9]{6,20}-[0-9]{2,8}-[0-9]+)"
        r"\b"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:

            return normalize_fbs(
                match.group(1)
            )

    return ""


# ============================================================
# РАЗБИВКА PDF НА СТРАНИЦЫ
# ============================================================

def split_label_pdf(pdf_bytes):

    reader = PdfReader(
        io.BytesIO(pdf_bytes)
    )

    pages = []

    for page_number, page in enumerate(
        reader.pages,
        start=1
    ):

        fbs = extract_fbs_from_page(
            page
        )

        writer = PdfWriter()

        writer.add_page(
            page
        )

        page_buffer = io.BytesIO()

        writer.write(
            page_buffer
        )

        pages.append({

            "page_number":
                page_number,

            "fbs":
                fbs,

            "pdf":
                page_buffer.getvalue()
        })

    return pages


# ============================================================
# ПОЛУЧЕНИЕ ОДНОЙ ЭТИКЕТКИ
# ============================================================

def get_single_label(posting_number):

    headers = get_headers()

    payload = {
        "posting_number": [
            posting_number
        ]
    }

    last_error = ""

    for attempt in range(
        1,
        LABEL_RETRY_COUNT + 1
    ):

        try:

            response = requests.post(
                LABEL_URL,
                headers=headers,
                json=payload,
                timeout=90
            )

            if response.status_code == 200:

                pdf_bytes = response.content

                if not pdf_bytes.startswith(
                    b"%PDF"
                ):

                    last_error = (
                        "Ozon вернул ответ, "
                        "который не является PDF."
                    )

                else:

                    pages = split_label_pdf(
                        pdf_bytes
                    )

                    if pages:

                        return pages, ""

                    last_error = (
                        "PDF от Ozon пустой."
                    )

            else:

                last_error = (
                    f"HTTP {response.status_code}: "
                    f"{response.text[:1000]}"
                )

        except Exception as e:

            last_error = str(e)

        if attempt < LABEL_RETRY_COUNT:

            time.sleep(
                LABEL_RETRY_DELAY
            )

    return [], last_error


# ============================================================
# ПОЛУЧЕНИЕ ЭТИКЕТОК
# ============================================================

def get_labels(posting_numbers):

    headers = get_headers()

    all_labels = []

    missing = []

    total = len(
        posting_numbers
    )

    progress = st.progress(0)

    status_box = st.empty()

    # ========================================================
    # Идём пачками по 20
    # ========================================================

    for start in range(
        0,
        total,
        LABEL_BATCH_SIZE
    ):

        batch = posting_numbers[
            start:
            start + LABEL_BATCH_SIZE
        ]

        status_box.write(
            f"🏷 Этикетки "
            f"{start + 1}–"
            f"{min(start + len(batch), total)} "
            f"из {total}"
        )

        batch_pages = []

        batch_ok = False

        # ====================================================
        # ПАКЕТНЫЙ ЗАПРОС
        # ====================================================

        try:

            response = requests.post(
                LABEL_URL,
                headers=headers,
                json={
                    "posting_number": batch
                },
                timeout=120
            )

            if response.status_code == 200:

                pdf_bytes = response.content

                if pdf_bytes.startswith(
                    b"%PDF"
                ):

                    batch_pages = (
                        split_label_pdf(
                            pdf_bytes
                        )
                    )

                    if batch_pages:

                        batch_ok = True

        except Exception:

            batch_ok = False

        # ====================================================
        # Если пакет не получен —
        # получаем по одной
        # ====================================================

        if not batch_ok:

            st.warning(
                f"Пакет {start + 1}–"
                f"{start + len(batch)} "
                f"не получен. "
                f"Пробуем по одной."
            )

            for local_index, posting_number in enumerate(
                batch
            ):

                global_index = (
                    start
                    + local_index
                    + 1
                )

                status_box.write(
                    f"🏷 Этикетка "
                    f"{global_index} "
                    f"из {total}: "
                    f"{posting_number}"
                )

                pages, error = (
                    get_single_label(
                        posting_number
                    )
                )

                if pages:

                    for page in pages:

                        all_labels.append({

                            "requested_posting":
                                posting_number,

                            "page_number":
                                page[
                                    "page_number"
                                ],

                            "fbs":
                                page[
                                    "fbs"
                                ],

                            "pdf":
                                page[
                                    "pdf"
                                ]
                        })

                else:

                    missing.append({

                        "posting_number":
                            posting_number,

                        "error":
                            error
                    })

        # ====================================================
        # ПАКЕТ ПОЛУЧЕН
        # ====================================================

        else:

            for page in batch_pages:

                all_labels.append({

                    "requested_posting":
                        "",

                    "page_number":
                        page[
                            "page_number"
                        ],

                    "fbs":
                        page[
                            "fbs"
                        ],

                    "pdf":
                        page[
                            "pdf"
                        ]
                })

        progress.progress(
            min(
                1.0,
                (
                    start
                    + len(batch)
                )
                / total
            )
        )

    progress.empty()
    status_box.empty()

    return (
        all_labels,
        missing
    )


# ============================================================
# ШРИФТ ДЛЯ ИНФОРМАЦИОННОЙ СТРАНИЦЫ
# ============================================================

def setup_font():

    font_paths = [

        "/usr/share/fonts/truetype/dejavu/"
        "DejaVuSans.ttf",

        "/usr/share/fonts/dejavu/"
        "DejaVuSans.ttf",

        "/usr/local/share/fonts/"
        "DejaVuSans.ttf",

        "/Library/Fonts/Arial.ttf",

        "/System/Library/Fonts/"
        "Supplemental/Arial.ttf"
    ]

    for font_path in font_paths:

        try:

            pdfmetrics.registerFont(
                TTFont(
                    "OzonFont",
                    font_path
                )
            )

            return "OzonFont"

        except Exception:

            continue

    return "Helvetica"


FONT_NAME = setup_font()


# ============================================================
# ИНФОРМАЦИОННАЯ СТРАНИЦА
# ============================================================

def create_info_page(
    posting_number,
    article,
    product,
    quantity,
    status
):

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
        FONT_NAME,
        24
    )

    c.drawString(
        50,
        height - 70,
        "OZON FBS"
    )

    c.setFont(
        FONT_NAME,
        17
    )

    c.drawString(
        50,
        height - 105,
        "Информация об отправлении"
    )

    # --------------------------------------------------------
    # Поля
    # --------------------------------------------------------

    y = height - 170

    fields = [

        (
            "Отправление",
            posting_number
        ),

        (
            "Артикул",
            article
        ),

        (
            "Товар",
            product
        ),

        (
            "Количество",
            quantity
        ),

        (
            "Статус",
            status
        )
    ]

    for title, value in fields:

        value = (
            ""
            if value is None
            else str(value)
        )

        c.setFont(
            FONT_NAME,
            11
        )

        c.drawString(
            50,
            y,
            title + ":"
        )

        c.setFont(
            FONT_NAME,
            15
        )

        # ----------------------------------------------------
        # Перенос длинного текста
        # ----------------------------------------------------

        if len(value) <= 60:

            c.drawString(
                170,
                y,
                value
            )

        else:

            first = value[:60]

            second = value[60:120]

            c.drawString(
                170,
                y,
                first
            )

            if second:

                y -= 20

                c.drawString(
                    170,
                    y,
                    second
                )

        y -= 48

    c.setFont(
        FONT_NAME,
        9
    )

    c.drawString(
        50,
        50,
        "Сформировано автоматически"
    )

    c.save()

    return buffer.getvalue()


# ============================================================
# ФИНАЛЬНЫЙ PDF
# ============================================================

def create_result_pdf(
    mapped_labels
):

    writer = PdfWriter()

    for item in mapped_labels:

        # ----------------------------------------------------
        # ЭТИКЕТКА
        # ----------------------------------------------------

        label_reader = PdfReader(
            io.BytesIO(
                item["pdf"]
            )
        )

        for page in label_reader.pages:

            writer.add_page(
                page
            )

        # ----------------------------------------------------
        # ИНФОРМАЦИОННАЯ СТРАНИЦА
        # ----------------------------------------------------

        info_pdf = create_info_page(

            posting_number=
                item[
                    "posting_number"
                ],

            article=
                item[
                    "article"
                ],

            product=
                item[
                    "product"
                ],

            quantity=
                item[
                    "quantity"
                ],

            status=
                item[
                    "status"
                ]
        )

        info_reader = PdfReader(
            io.BytesIO(
                info_pdf
            )
        )

        writer.add_page(
            info_reader.pages[0]
        )

    result = io.BytesIO()

    writer.write(
        result
    )

    return result.getvalue()


# ============================================================
# ОСНОВНОЙ ЗАПУСК
# ============================================================

st.divider()

st.subheader(
    "📅 Сборка за "
    + selected_date.strftime(
        "%d.%m.%Y"
    )
)


if st.button(
    "🚀 Получить отправления и этикетки",
    type="primary",
    use_container_width=True
):

    # ========================================================
    # 1. ПОЛУЧАЕМ ОТПРАВЛЕНИЯ
    # ========================================================

    st.subheader(
        "1️⃣ Получение отправлений"
    )

    try:

        raw_postings = get_postings(
            selected_date
        )

    except Exception as e:

        st.error(
            str(e)
        )

        st.stop()

    # --------------------------------------------------------
    # НОРМАЛИЗУЕМ
    # --------------------------------------------------------

    posting_rows = normalize_postings(
        raw_postings
    )

    # --------------------------------------------------------
    # DEBUG — только если ничего не получили
    # --------------------------------------------------------

    if not raw_postings:

        st.error(
            "Ozon API вернул 0 отправлений."
        )

        st.warning(
            "Это проблема получения отправлений, "
            "а не маппинга этикеток."
        )

        st.info(
            "Запрос выполнялся с расширенным окном "
            "за день до выбранной даты "
            "до дня после выбранной даты."
        )

        st.stop()

    # --------------------------------------------------------
    # МАППИНГ POSTING NUMBER → ДАННЫЕ
    # --------------------------------------------------------

    postings_map = {}

    for row in posting_rows:

        posting_number = normalize_fbs(
            row[
                "posting_number"
            ]
        )

        if not posting_number:
            continue

        if posting_number not in postings_map:

            postings_map[
                posting_number
            ] = []

        postings_map[
            posting_number
        ].append(
            row
        )

    posting_numbers = list(
        postings_map.keys()
    )

    # --------------------------------------------------------
    # Если API вернул postings,
    # но не удалось вытащить posting_number
    # --------------------------------------------------------

    if not posting_numbers:

        st.error(
            "Ozon вернул отправления, "
            "но из них не удалось получить "
            "posting_number."
        )

        st.write(
            "Количество объектов от Ozon:",
            len(raw_postings)
        )

        # Показываем структуру только для диагностики
        with st.expander(
            "🔎 Показать ответ первого отправления"
        ):

            st.json(
                raw_postings[0]
            )

        st.stop()

    # --------------------------------------------------------
    # УСПЕХ
    # --------------------------------------------------------

    st.success(
        f"✅ Найдено отправлений: "
        f"**{len(posting_numbers)}**"
    )

    # ========================================================
    # ТАБЛИЦА ОТПРАВЛЕНИЙ
    # ========================================================

    table_rows = []

    for row in posting_rows:

        table_rows.append({

            "Отправление":
                row[
                    "posting_number"
                ],

            "Артикул":
                row[
                    "article"
                ],

            "Товар":
                row[
                    "product"
                ],

            "Кол-во":
                row[
                    "quantity"
                ],

            "Статус":
                row[
                    "status"
                ]
        })

    if table_rows:

        st.dataframe(
            pd.DataFrame(
                table_rows
            ),
            use_container_width=True,
            hide_index=True
        )

    # ========================================================
    # 2. ПОЛУЧАЕМ ЭТИКЕТКИ
    # ========================================================

    st.subheader(
        "2️⃣ Получение этикеток"
    )

    labels, missing_labels = get_labels(
        posting_numbers
    )

    # ========================================================
    # 3. ТОЧНЫЙ МАППИНГ
    # ========================================================

    st.subheader(
        "3️⃣ Маппинг по номеру FBS"
    )

    mapped_labels = []

    unknown_labels = []

    used_fbs = set()

    for label in labels:

        # ----------------------------------------------------
        # FBS ИЗ САМОЙ ЭТИКЕТКИ
        # ----------------------------------------------------

        fbs = normalize_fbs(
            label[
                "fbs"
            ]
        )

        # ----------------------------------------------------
        # НЕ НАШЛИ FBS
        # ----------------------------------------------------

        if not fbs:

            unknown_labels.append(
                label
            )

            continue

        # ----------------------------------------------------
        # НАШЛИ ОТПРАВЛЕНИЕ
        # ----------------------------------------------------

        posting_info = postings_map.get(
            fbs
        )

        if not posting_info:

            unknown_labels.append(
                label
            )

            continue

        used_fbs.add(
            fbs
        )

        # ----------------------------------------------------
        # ТОВАРЫ В ОТПРАВЛЕНИИ
        # ----------------------------------------------------

        for row in posting_info:

            mapped_labels.append({

                "posting_number":
                    fbs,

                "article":
                    row[
                        "article"
                    ],

                "product":
                    row[
                        "product"
                    ],

                "quantity":
                    row[
                        "quantity"
                    ],

                "status":
                    row[
                        "status"
                    ],

                "page_number":
                    label[
                        "page_number"
                    ],

                "pdf":
                    label[
                        "pdf"
                    ]
            })

    # ========================================================
    # ТАБЛИЦА МАППИНГА
    # ========================================================

    mapping_rows = []

    for item in mapped_labels:

        mapping_rows.append({

            "Отправление":
                item[
                    "posting_number"
                ],

            "Артикул":
                item[
                    "article"
                ],

            "Товар":
                item[
                    "product"
                ],

            "Кол-во":
                item[
                    "quantity"
                ],

            "Статус":
                item[
                    "status"
                ],

            "Этикетка":
                "✅ Найдена",

            "Страница":
                item[
                    "page_number"
                ]
        })

    if mapping_rows:

        st.dataframe(
            pd.DataFrame(
                mapping_rows
            ),
            use_container_width=True,
            hide_index=True
        )

    else:

        st.error(
            "Не удалось сопоставить ни одной "
            "этикетки с отправлением."
        )

    # ========================================================
    # МЕТРИКИ
    # ========================================================

    st.divider()

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Отправлений",
        len(posting_numbers)
    )

    col2.metric(
        "Страниц этикеток",
        len(labels)
    )

    col3.metric(
        "Сопоставлено",
        len(used_fbs)
    )

    col4.metric(
        "Без маппинга",
        len(unknown_labels)
    )

    # ========================================================
    # НЕСОПОСТАВЛЕННЫЕ ЭТИКЕТКИ
    # ========================================================

    if unknown_labels:

        st.warning(
            f"⚠️ Не сопоставлено "
            f"{len(unknown_labels)} "
            f"страниц этикеток."
        )

        unknown_rows = []

        for item in unknown_labels:

            # Здесь НЕ показываем текст PDF.
            # Только техническая информация.

            unknown_rows.append({

                "Страница":
                    item[
                        "page_number"
                    ],

                "FBS":
                    item[
                        "fbs"
                    ]
                    or "Не найден"
            })

        st.dataframe(
            pd.DataFrame(
                unknown_rows
            ),
            use_container_width=True,
            hide_index=True
        )

    # ========================================================
    # ОТПРАВЛЕНИЯ БЕЗ ЭТИКЕТОК
    # ========================================================

    if missing_labels:

        st.warning(
            f"⚠️ "
            f"{len(missing_labels)} "
            f"отправлений без этикетки."
        )

        missing_rows = []

        for item in missing_labels:

            posting_number = item[
                "posting_number"
            ]

            rows = postings_map.get(
                posting_number,
                []
            )

            if rows:

                row = rows[0]

                missing_rows.append({

                    "Отправление":
                        posting_number,

                    "Артикул":
                        row[
                            "article"
                        ],

                    "Товар":
                        row[
                            "product"
                        ],

                    "Кол-во":
                        row[
                            "quantity"
                        ],

                    "Статус":
                        row[
                            "status"
                        ],

                    "Причина":
                        item[
                            "error"
                        ]
                })

            else:

                missing_rows.append({

                    "Отправление":
                        posting_number,

                    "Артикул":
                        "",

                    "Товар":
                        "",

                    "Кол-во":
                        "",

                    "Статус":
                        "",

                    "Причина":
                        item[
                            "error"
                        ]
                })

        st.dataframe(
            pd.DataFrame(
                missing_rows
            ),
            use_container_width=True,
            hide_index=True
        )

    # ========================================================
    # 4. ФИНАЛЬНЫЙ PDF
    # ========================================================

    if mapped_labels:

        st.subheader(
            "4️⃣ PDF для сборки"
        )

        with st.spinner(
            "Формируем итоговый PDF..."
        ):

            result_pdf = (
                create_result_pdf(
                    mapped_labels
                )
            )

        st.success(
            f"✅ Готово. "
            f"Сопоставлено отправлений: "
            f"{len(used_fbs)}"
        )

        st.download_button(

            label=
                "📥 Скачать PDF для сборки",

            data=
                result_pdf,

            file_name=
                "Ozon_FBS_"
                + selected_date.strftime(
                    "%Y-%m-%d"
                )
                + ".pdf",

            mime=
                "application/pdf",

            use_container_width=True
        )

    else:

        st.error(
            "PDF не сформирован: "
            "нет сопоставленных этикеток."
        )
