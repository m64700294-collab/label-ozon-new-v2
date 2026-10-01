import io
import re
import time
import requests
import streamlit as st
import pandas as pd

from datetime import datetime
from pypdf import PdfReader, PdfWriter

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# НАСТРОЙКИ
# ============================================================

POSTINGS_URL = "https://api-seller.ozon.ru/v1/assembly/fbs/posting/list"
LABEL_URL = "https://api-seller.ozon.ru/v2/posting/fbs/package-label"

# Максимум отправлений в одном запросе этикеток
BATCH_SIZE = 20

# Если этикетка ещё не готова
LABEL_RETRY_COUNT = 3
LABEL_RETRY_DELAY = 2


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
    "Получение отправлений за выбранную дату → "
    "этикетки → точный маппинг по номеру FBS"
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
    "По умолчанию — сегодня."
)


# ============================================================
# ЗАГОЛОВКИ API
# ============================================================

def get_headers():
    return {
        "Client-Id": str(client_id),
        "Api-Key": str(api_key),
        "Content-Type": "application/json"
    }


# ============================================================
# ПОЛУЧЕНИЕ ОТПРАВЛЕНИЙ
# ============================================================

def get_postings(date_value):

    start_dt = datetime.combine(
        date_value,
        datetime.min.time()
    )

    end_dt = datetime.combine(
        date_value,
        datetime.max.time()
    )

    cutoff_from = (
        start_dt.strftime("%Y-%m-%dT%H:%M:%S")
        + "Z"
    )

    cutoff_to = (
        end_dt.strftime("%Y-%m-%dT%H:%M:%S")
        + "Z"
    )

    headers = get_headers()

    all_postings = []

    cursor = ""

    progress = st.progress(0)
    status_box = st.empty()

    page_number = 0

    while True:

        page_number += 1

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
            f"📥 Получение отправлений... "
            f"страница {page_number}"
        )

        try:
            response = requests.post(
                POSTINGS_URL,
                headers=headers,
                json=payload,
                timeout=60
            )

        except Exception as e:

            progress.empty()
            status_box.empty()

            raise Exception(
                f"Ошибка соединения с Ozon API:\n{e}"
            )

        if response.status_code != 200:

            progress.empty()
            status_box.empty()

            raise Exception(
                f"Ozon API HTTP {response.status_code}\n\n"
                f"{response.text}"
            )

        try:
            data = response.json()
        except Exception:
            raise Exception(
                "Ozon вернул некорректный JSON:\n"
                + response.text[:2000]
            )

        result = data.get("result", {})

        if isinstance(result, dict):

            postings = (
                result.get("postings")
                or result.get("items")
                or []
            )

            next_cursor = (
                result.get("cursor")
                or data.get("cursor")
                or ""
            )

        elif isinstance(result, list):

            postings = result

            next_cursor = (
                data.get("cursor")
                or ""
            )

        else:

            postings = []

            next_cursor = ""

        all_postings.extend(postings)

        if not next_cursor:
            break

        if next_cursor == cursor:
            break

        cursor = next_cursor

        progress.progress(
            min(
                0.95,
                0.1 + page_number * 0.05
            )
        )

        # защита
        if page_number >= 100:
            break

    progress.progress(1)
    progress.empty()
    status_box.empty()

    return all_postings


# ============================================================
# ПРЕОБРАЗОВАНИЕ ОТПРАВЛЕНИЙ
# ============================================================

def convert_posting(posting):

    posting_number = (
        posting.get("posting_number")
        or posting.get("postingNumber")
        or posting.get("number")
        or ""
    )

    status = (
        posting.get("status")
        or posting.get("status_name")
        or ""
    )

    products = (
        posting.get("products")
        or []
    )

    if not isinstance(products, list):
        products = []

    rows = []

    if products:

        for product in products:

            if not isinstance(product, dict):
                continue

            article = (
                product.get("offer_id")
                or product.get("offerId")
                or product.get("sku")
                or ""
            )

            product_name = (
                product.get("name")
                or product.get("product_name")
                or ""
            )

            quantity = (
                product.get("quantity")
                or product.get("qty")
                or 1
            )

            rows.append({
                "posting_number": str(
                    posting_number
                ),
                "article": str(
                    article
                ),
                "product": str(
                    product_name
                ),
                "quantity": quantity,
                "status": str(
                    status
                )
            })

    else:

        article = (
            posting.get("offer_id")
            or posting.get("offerId")
            or posting.get("sku")
            or ""
        )

        product_name = (
            posting.get("name")
            or posting.get("product_name")
            or ""
        )

        quantity = (
            posting.get("quantity")
            or posting.get("qty")
            or 1
        )

        rows.append({
            "posting_number": str(
                posting_number
            ),
            "article": str(
                article
            ),
            "product": str(
                product_name
            ),
            "quantity": quantity,
            "status": str(
                status
            )
        })

    return rows


def normalize_postings(raw_postings):

    rows = []

    for posting in raw_postings:

        rows.extend(
            convert_posting(posting)
        )

    return rows


# ============================================================
# НОРМАЛИЗАЦИЯ НОМЕРА FBS
# ============================================================

def normalize_fbs(value):

    if value is None:
        return ""

    value = str(value).strip()

    # Убираем пробелы
    value = re.sub(
        r"\s+",
        "",
        value
    )

    return value


# ============================================================
# ИЗВЛЕЧЕНИЕ FBS ИЗ ЭТИКЕТКИ
# ============================================================

def extract_fbs_from_page(page):

    """
    Ищем внутри страницы этикетки строку примерно:

        FBS: 1123559 0128758204-0424-1

    Нам нужен:

        0128758204-0424-1

    ВАЖНО:
    весь текст этикетки пользователю НЕ показываем.
    Из неё извлекается только номер FBS.
    """

    try:
        text = page.extract_text() or ""
    except Exception:
        text = ""

    if not text:
        return ""

    # --------------------------------------------------------
    # Вариант 1
    # FBS: 1123559 0128758204-0424-1
    # --------------------------------------------------------

    patterns = [

        r"FBS\s*:\s*\d+\s+([0-9]{8,15}-[0-9]{3,6}-[0-9]+)",

        r"FBS\s*[:\-]?\s*([0-9]{8,15}-[0-9]{3,6}-[0-9]+)",

        r"\b([0-9]{8,15}-[0-9]{3,6}-[0-9]+)\b"
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
# РАЗБИВАЕМ ПОЛУЧЕННЫЙ PDF ЭТИКЕТОК
# ============================================================

def split_label_pdf(pdf_bytes):

    """
    Получаем PDF от Ozon и каждую страницу
    превращаем в отдельный PDF.

    Одновременно определяем FBS прямо
    из текста страницы.
    """

    reader = PdfReader(
        io.BytesIO(pdf_bytes)
    )

    result = []

    for page_number, page in enumerate(
        reader.pages,
        start=1
    ):

        # ----------------------------------------------------
        # ИЗВЛЕКАЕМ FBS
        # ----------------------------------------------------

        fbs_number = extract_fbs_from_page(
            page
        )

        # ----------------------------------------------------
        # СОХРАНЯЕМ ОТДЕЛЬНУЮ СТРАНИЦУ
        # ----------------------------------------------------

        writer = PdfWriter()

        writer.add_page(page)

        page_buffer = io.BytesIO()

        writer.write(
            page_buffer
        )

        result.append({
            "page_number": page_number,
            "fbs": fbs_number,
            "pdf": page_buffer.getvalue()
        })

    return result


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
                timeout=60
            )

            if response.status_code == 200:

                pdf_bytes = response.content

                if pdf_bytes.startswith(
                    b"%PDF"
                ):

                    pages = split_label_pdf(
                        pdf_bytes
                    )

                    if pages:

                        return pages, ""

                    last_error = (
                        "Ozon вернул пустой PDF"
                    )

                else:

                    last_error = (
                        "Ответ Ozon не является PDF"
                    )

            else:

                last_error = (
                    f"HTTP {response.status_code}: "
                    f"{response.text}"
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

    for start in range(
        0,
        total,
        BATCH_SIZE
    ):

        batch = posting_numbers[
            start:start + BATCH_SIZE
        ]

        status_box.write(
            f"🏷 Получение этикеток "
            f"{start + 1}–"
            f"{min(start + len(batch), total)} "
            f"из {total}"
        )

        payload = {
            "posting_number": batch
        }

        batch_pages = []
        batch_ok = False

        # ====================================================
        # ПЫТАЕМСЯ ПОЛУЧИТЬ ПАКЕТ
        # ====================================================

        try:

            response = requests.post(
                LABEL_URL,
                headers=headers,
                json=payload,
                timeout=120
            )

            if response.status_code == 200:

                pdf_bytes = response.content

                if pdf_bytes.startswith(
                    b"%PDF"
                ):

                    pages = split_label_pdf(
                        pdf_bytes
                    )

                    # Здесь нам НЕ важно,
                    # в каком порядке страницы.
                    #
                    # Важно только то, что внутри
                    # каждой страницы есть собственный FBS.

                    if pages:

                        batch_pages = pages
                        batch_ok = True

        except Exception:
            batch_ok = False

        # ====================================================
        # ЕСЛИ ПАКЕТ НЕ ПОЛУЧИЛСЯ
        # ====================================================

        if not batch_ok:

            st.warning(
                f"Пакет {start + 1}–"
                f"{start + len(batch)} "
                f"не получен. "
                f"Пробуем отправления по одному."
            )

            for index, posting_number in enumerate(
                batch
            ):

                status_box.write(
                    f"🏷 Этикетка "
                    f"{start + index + 1} "
                    f"из {total}: "
                    f"{posting_number}"
                )

                pages, error = get_single_label(
                    posting_number
                )

                if pages:

                    for page in pages:

                        all_labels.append({
                            "requested_posting": posting_number,
                            "page_number": page[
                                "page_number"
                            ],
                            "fbs": page["fbs"],
                            "pdf": page["pdf"]
                        })

                else:

                    missing.append({
                        "posting_number":
                            posting_number,
                        "error": error
                    })

        # ====================================================
        # ПАКЕТ ПОЛУЧЕН
        # ====================================================

        else:

            for page in batch_pages:

                all_labels.append({
                    "requested_posting": "",
                    "page_number": page[
                        "page_number"
                    ],
                    "fbs": page["fbs"],
                    "pdf": page["pdf"]
                })

        progress.progress(
            min(
                1.0,
                (start + len(batch))
                / total
            )
        )

    progress.empty()
    status_box.empty()

    return all_labels, missing


# ============================================================
# ПОИСК ШРИФТА
# ============================================================

def setup_font():

    font_paths = [

        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",

        "/usr/share/fonts/dejavu/DejaVuSans.ttf",

        "/usr/local/share/fonts/DejaVuSans.ttf",

        "/Library/Fonts/Arial.ttf",

        "/System/Library/Fonts/Supplemental/Arial.ttf"
    ]

    for font_path in font_paths:

        try:

            pdfmetrics.registerFont(
                TTFont(
                    "OzonDejaVu",
                    font_path
                )
            )

            return "OzonDejaVu"

        except Exception:
            pass

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
        22
    )

    c.drawString(
        50,
        height - 70,
        "OZON FBS"
    )

    c.setFont(
        FONT_NAME,
        18
    )

    c.drawString(
        50,
        height - 105,
        "Информация об отправлении"
    )

    # --------------------------------------------------------
    # Данные
    # --------------------------------------------------------

    y = height - 170

    rows = [
        ("Отправление", posting_number),
        ("Артикул", article),
        ("Товар", product),
        ("Количество", quantity),
        ("Статус", status)
    ]

    for title, value in rows:

        value = "" if value is None else str(value)

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
        # Разбиваем длинное название товара
        # ----------------------------------------------------

        if len(value) > 65:

            first_line = value[:65]
            second_line = value[65:130]

            c.drawString(
                170,
                y,
                first_line
            )

            if second_line:

                y -= 20

                c.drawString(
                    170,
                    y,
                    second_line
                )

        else:

            c.drawString(
                170,
                y,
                value
            )

        y -= 45

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

            writer.add_page(page)

        # ----------------------------------------------------
        # ИНФОРМАЦИОННАЯ СТРАНИЦА
        # ----------------------------------------------------

        info_pdf = create_info_page(
            posting_number=item[
                "posting_number"
            ],
            article=item[
                "article"
            ],
            product=item[
                "product"
            ],
            quantity=item[
                "quantity"
            ],
            status=item[
                "status"
            ]
        )

        info_reader = PdfReader(
            io.BytesIO(info_pdf)
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
# ОСНОВНАЯ ЛОГИКА
# ============================================================

if not client_id or not api_key:

    st.info(
        "Введите Client-Id и Api-Key "
        "в боковой панели."
    )

    st.stop()


st.divider()

st.subheader(
    "📅 "
    + selected_date.strftime("%d.%m.%Y")
)


if st.button(
    "🚀 Получить отправления и этикетки",
    type="primary",
    use_container_width=True
):

    # ========================================================
    # 1. ОТПРАВЛЕНИЯ
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

    posting_rows = normalize_postings(
        raw_postings
    )

    # --------------------------------------------------------
    # МАППИНГ API
    # --------------------------------------------------------

    postings_map = {}

    for row in posting_rows:

        posting_number = normalize_fbs(
            row["posting_number"]
        )

        if not posting_number:
            continue

        if posting_number not in postings_map:

            postings_map[
                posting_number
            ] = []

        postings_map[
            posting_number
        ].append(row)

    posting_numbers = list(
        postings_map.keys()
    )

    st.success(
        f"Найдено отправлений: "
        f"**{len(posting_numbers)}**"
    )

    # --------------------------------------------------------
    # Таблица отправлений
    # --------------------------------------------------------

    if posting_rows:

        df = pd.DataFrame(
            posting_rows
        )

        df = df.rename(
            columns={
                "posting_number":
                    "Отправление",
                "article":
                    "Артикул",
                "product":
                    "Товар",
                "quantity":
                    "Кол-во",
                "status":
                    "Статус"
            }
        )

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True
        )

    if not posting_numbers:

        st.warning(
            "За выбранную дату "
            "отправлений не найдено."
        )

        st.stop()

    # ========================================================
    # 2. ЭТИКЕТКИ
    # ========================================================

    st.subheader(
        "2️⃣ Получение этикеток"
    )

    labels, missing_api = get_labels(
        posting_numbers
    )

    # ========================================================
    # 3. МАППИНГ ПО FBS ВНУТРИ ЭТИКЕТКИ
    # ========================================================

    st.subheader(
        "3️⃣ Точный маппинг этикеток"
    )

    mapped_labels = []

    unknown_labels = []

    for label in labels:

        # ----------------------------------------------------
        # FBS из самой этикетки
        # ----------------------------------------------------

        fbs = normalize_fbs(
            label["fbs"]
        )

        # ----------------------------------------------------
        # Если FBS не найден
        # ----------------------------------------------------

        if not fbs:

            unknown_labels.append(
                label
            )

            continue

        # ----------------------------------------------------
        # Ищем отправление API
        # ----------------------------------------------------

        posting_info = postings_map.get(
            fbs
        )

        if not posting_info:

            unknown_labels.append(
                label
            )

            continue

        # ----------------------------------------------------
        # Может быть несколько товаров
        # ----------------------------------------------------

        for row in posting_info:

            mapped_labels.append({
                "posting_number": fbs,
                "article": row[
                    "article"
                ],
                "product": row[
                    "product"
                ],
                "quantity": row[
                    "quantity"
                ],
                "status": row[
                    "status"
                ],
                "pdf": label[
                    "pdf"
                ],
                "page_number": label[
                    "page_number"
                ]
            })

    # ========================================================
    # ТАБЛИЦА МАППИНГА
    # ========================================================

    mapping_rows = []

    for item in mapped_labels:

        mapping_rows.append({
            "Отправление": item[
                "posting_number"
            ],
            "Артикул": item[
                "article"
            ],
            "Товар": item[
                "product"
            ],
            "Кол-во": item[
                "quantity"
            ],
            "Статус": item[
                "status"
            ],
            "Этикетка": "✅ Найдена",
            "Страница PDF": item[
                "page_number"
            ]
        })

    if mapping_rows:

        df_mapping = pd.DataFrame(
            mapping_rows
        )

        st.dataframe(
            df_mapping,
            use_container_width=True,
            hide_index=True
        )

    # ========================================================
    # МЕТРИКИ
    # ========================================================

    st.divider()

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Отправлений API",
        len(posting_numbers)
    )

    col2.metric(
        "Этикеток получено",
        len(labels)
    )

    col3.metric(
        "Сопоставлено",
        len(mapped_labels)
    )

    col4.metric(
        "Не сопоставлено",
        len(unknown_labels)
    )

    # ========================================================
    # НЕСОПОСТАВЛЕННЫЕ ЭТИКЕТКИ
    # ========================================================

    if unknown_labels:

        st.warning(
            f"⚠️ {len(unknown_labels)} "
            f"этикеток не удалось сопоставить "
            f"с отправлениями API."
        )

        unknown_rows = []

        for label in unknown_labels:

            unknown_rows.append({
                "FBS из этикетки":
                    label["fbs"]
                    or "Не найден",
                "Страница PDF":
                    label["page_number"]
            })

        st.dataframe(
            pd.DataFrame(
                unknown_rows
            ),
            use_container_width=True,
            hide_index=True
        )

    # ========================================================
    # ОТСУТСТВУЮЩИЕ ЭТИКЕТКИ
    # ========================================================

    if missing_api:

        st.warning(
            f"⚠️ {len(missing_api)} "
            f"отправлений не получили этикетку."
        )

        missing_rows = []

        for item in missing_api:

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
                        row["article"],
                    "Товар":
                        row["product"],
                    "Кол-во":
                        row["quantity"],
                    "Статус":
                        row["status"],
                    "Ошибка":
                        item["error"]
                })

            else:

                missing_rows.append({
                    "Отправление":
                        posting_number,
                    "Артикул": "",
                    "Товар": "",
                    "Кол-во": "",
                    "Статус": "",
                    "Ошибка":
                        item["error"]
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
            "4️⃣ Готовый PDF для сборки"
        )

        with st.spinner(
            "Формируем PDF..."
        ):

            result_pdf = create_result_pdf(
                mapped_labels
            )

        st.success(
            "✅ PDF сформирован."
        )

        st.download_button(
            label="📥 Скачать PDF для сборки",
            data=result_pdf,
            file_name=(
                "Ozon_FBS_"
                + selected_date.strftime(
                    "%Y-%m-%d"
                )
                + ".pdf"
            ),
            mime="application/pdf",
            use_container_width=True
        )

    else:

        st.error(
            "❌ Нет этикеток, которые удалось "
            "сопоставить с отправлениями."
        )
