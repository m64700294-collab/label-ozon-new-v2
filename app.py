import streamlit as st
import requests
import io
import time
import base64

from datetime import datetime, timedelta

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# CONFIG
# ============================================================

st.set_page_config(
    page_title="Ozon FBS — Отправления и этикетки",
    page_icon="🏷️",
    layout="wide"
)


API_BASE = "https://api-seller.ozon.ru"

POSTINGS_URL = (
    API_BASE +
    "/v1/assembly/fbs/posting/list"
)

LABEL_URL = (
    API_BASE +
    "/v2/posting/fbs/package-label"
)


# ============================================================
# FONTS
# ============================================================

def register_fonts():

    regular = "Helvetica"
    bold = "Helvetica-Bold"

    paths = [
        (
            "AppFont",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
        (
            "AppFontBold",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        )
    ]

    for name, path in paths:

        try:

            pdfmetrics.registerFont(
                TTFont(name, path)
            )

            if name == "AppFont":
                regular = name

            if name == "AppFontBold":
                bold = name

        except Exception:
            pass

    return regular, bold


FONT_REGULAR, FONT_BOLD = register_fonts()


# ============================================================
# HELPERS
# ============================================================

def normalize(value):

    if value is None:
        return ""

    return str(value).strip()


def chunks(items, size):

    for i in range(
        0,
        len(items),
        size
    ):
        yield items[
            i:i + size
        ]


# ============================================================
# API REQUEST
# ============================================================

def api_headers(
    client_id,
    api_key
):

    return {
        "Client-Id": str(
            client_id
        ).strip(),

        "Api-Key": str(
            api_key
        ).strip(),

        "Content-Type":
            "application/json"
    }


# ============================================================
# 1. ПОЛУЧАЕМ ОТПРАВЛЕНИЯ
# ============================================================

def get_postings(
    client_id,
    api_key,
    date_from,
    date_to
):

    headers = api_headers(
        client_id,
        api_key
    )

    all_postings = []

    cursor = ""

    while True:

        payload = {
            "filter": {
                "cutoff_from": date_from,
                "cutoff_to": date_to
            },

            "limit": 1000,

            "sort_dir": "ASC"
        }

        if cursor:
            payload["cursor"] = cursor

        response = requests.post(
            POSTINGS_URL,
            headers=headers,
            json=payload,
            timeout=60
        )

        if response.status_code != 200:

            try:
                error = response.json()
            except Exception:
                error = response.text

            raise RuntimeError(
                "Ошибка получения отправлений: "
                f"HTTP {response.status_code}\n"
                f"{error}"
            )

        data = response.json()

        postings = data.get(
            "postings",
            []
        )

        if not postings:
            break

        all_postings.extend(
            postings
        )

        next_cursor = data.get(
            "cursor"
        )

        if not next_cursor:
            break

        if next_cursor == cursor:
            break

        cursor = next_cursor

        if len(postings) < 1000:
            break

    return all_postings


# ============================================================
# 2. ПРЕОБРАЗУЕМ ОТПРАВЛЕНИЕ
# ============================================================

def convert_posting(
    posting
):

    posting_number = (
        posting.get(
            "posting_number"
        )
        or
        posting.get(
            "order_number"
        )
        or
        ""
    )

    products = (
        posting.get(
            "products"
        )
        or []
    )

    articles = []
    names = []

    quantities = []

    for product in products:

        if not isinstance(
            product,
            dict
        ):
            continue

        article = (
            product.get(
                "offer_id"
            )
            or
            product.get(
                "offerId"
            )
            or
            product.get(
                "sku"
            )
            or
            ""
        )

        name = (
            product.get(
                "name"
            )
            or
            product.get(
                "product_name"
            )
            or
            ""
        )

        quantity = (
            product.get(
                "quantity"
            )
            or 0
        )

        if article:
            articles.append(
                str(article)
            )

        if name:
            names.append(
                str(name)
            )

        try:

            quantities.append(
                float(quantity)
            )

        except Exception:
            pass

    total_quantity = sum(
        quantities
    )

    if total_quantity.is_integer():
        total_quantity = int(
            total_quantity
        )

    return {

        "posting_number":
            str(posting_number),

        "article":
            " + ".join(
                articles
            ) or "-",

        "product":
            " + ".join(
                names
            ) or "-",

        "quantity":
            total_quantity,

        "status":
            posting.get(
                "status",
                ""
            ),

        "raw":
            posting
    }


# ============================================================
# 3. ПОЛУЧАЕМ PDF ЭТИКЕТОК
# ============================================================

def get_labels_for_postings(
    client_id,
    api_key,
    posting_numbers,
    progress_callback=None
):

    headers = api_headers(
        client_id,
        api_key
    )

    all_label_pages = []

    label_records = []

    batches = list(
        chunks(
            posting_numbers,
            20
        )
    )

    total_batches = len(
        batches
    )

    for batch_index, batch in enumerate(
        batches,
        start=1
    ):

        # ----------------------------------------------------
        # Сначала пытаемся получить пачку из 20
        # ----------------------------------------------------

        payload = {
            "posting_number": batch
        }

        response = requests.post(
            LABEL_URL,
            headers={
                **headers,
                "Accept": "application/pdf"
            },
            json=payload,
            timeout=120
        )

        if response.status_code == 200:

            pdf_bytes = response.content

            reader = PdfReader(
                io.BytesIO(
                    pdf_bytes
                )
            )

            pages_count = len(
                reader.pages
            )

            # ------------------------------------------------
            # Нормальный случай:
            # количество страниц = количество отправлений
            # ------------------------------------------------

            if pages_count == len(batch):

                for i, page in enumerate(
                    reader.pages
                ):

                    label_records.append({
                        "posting_number":
                            batch[i],

                        "page":
                            page,

                        "source":
                            "batch"
                    })

            else:

                # ------------------------------------------------
                # Если количество страниц не совпало,
                # безопаснее получить этикетки по одной.
                # ------------------------------------------------

                for posting_number in batch:

                    single_result = (
                        get_single_label(
                            client_id,
                            api_key,
                            posting_number
                        )
                    )

                    if single_result:

                        label_records.append({
                            "posting_number":
                                posting_number,

                            "page":
                                single_result,

                            "source":
                                "single"
                        })

        else:

            # ------------------------------------------------
            # Если вся пачка не прошла,
            # пробуем каждое отправление отдельно.
            # ------------------------------------------------

            for posting_number in batch:

                single_result = (
                    get_single_label(
                        client_id,
                        api_key,
                        posting_number
                    )
                )

                if single_result:

                    label_records.append({
                        "posting_number":
                            posting_number,

                        "page":
                            single_result,

                        "source":
                            "single"
                    })

        if progress_callback:

            progress_callback(
                batch_index /
                total_batches,
                (
                    f"Этикетки: пачка "
                    f"{batch_index} из "
                    f"{total_batches}"
                )
            )

    return label_records


# ============================================================
# ПОЛУЧИТЬ ОДНУ ЭТИКЕТКУ
# ============================================================

def get_single_label(
    client_id,
    api_key,
    posting_number
):

    headers = api_headers(
        client_id,
        api_key
    )

    payload = {
        "posting_number": [
            posting_number
        ]
    }

    # --------------------------------------------------------
    # Иногда Ozon ещё не успел сформировать этикетку.
    # Пробуем несколько раз.
    # --------------------------------------------------------

    max_attempts = 3

    for attempt in range(
        1,
        max_attempts + 1
    ):

        response = requests.post(
            LABEL_URL,
            headers={
                **headers,
                "Accept": "application/pdf"
            },
            json=payload,
            timeout=60
        )

        if response.status_code == 200:

            try:

                reader = PdfReader(
                    io.BytesIO(
                        response.content
                    )
                )

                if len(
                    reader.pages
                ) > 0:

                    return reader.pages[0]

            except Exception:

                return None

        # ----------------------------------------------------
        # Этикетка может быть ещё не готова.
        # ----------------------------------------------------

        if attempt < max_attempts:

            time.sleep(2)

    return None


# ============================================================
# 4. СОЗДАЁМ ИНФОРМАЦИОННУЮ СТРАНИЦУ
# ============================================================

def create_info_page(
    width,
    height,
    posting
):

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=(
            width,
            height
        )
    )

    # --------------------------------------------------------
    # Заголовок
    # --------------------------------------------------------

    c.setFont(
        FONT_BOLD,
        16
    )

    c.drawCentredString(
        width / 2,
        height - 40,
        "ИНФОРМАЦИЯ ОТПРАВЛЕНИЯ"
    )

    y = height - 85

    # --------------------------------------------------------
    # Отправление
    # --------------------------------------------------------

    fields = [

        (
            "Отправление",
            posting[
                "posting_number"
            ]
        ),

        (
            "Артикул",
            posting[
                "article"
            ]
        ),

        (
            "Товар",
            posting[
                "product"
            ]
        ),

        (
            "Количество",
            posting[
                "quantity"
            ]
        ),

        (
            "Статус",
            posting[
                "status"
            ]
        )
    ]

    for label, value in fields:

        c.setFont(
            FONT_BOLD,
            10
        )

        c.drawString(
            30,
            y,
            f"{label}:"
        )

        y -= 16

        c.setFont(
            FONT_REGULAR,
            9
        )

        text = str(
            value
        )

        # Разбиваем длинный текст
        # примерно по 60 символов

        while len(text) > 60:

            part = text[:60]

            # Ищем последний пробел
            pos = part.rfind(" ")

            if pos > 0:
                part = text[:pos]

            c.drawString(
                30,
                y,
                part
            )

            y -= 13

            text = text[
                len(part):
            ].strip()

        if text:

            c.drawString(
                30,
                y,
                text
            )

            y -= 13

        y -= 10

    c.showPage()
    c.save()

    buffer.seek(0)

    return buffer


# ============================================================
# 5. ФОРМИРУЕМ ИТОГОВЫЙ PDF
# ============================================================

def create_result_pdf(
    label_records,
    postings_map
):

    writer = PdfWriter()

    mapping_rows = []

    for index, label in enumerate(
        label_records,
        start=1
    ):

        posting_number = (
            label[
                "posting_number"
            ]
        )

        posting = postings_map.get(
            posting_number
        )

        if not posting:
            continue

        page = label[
            "page"
        ]

        width = float(
            page.mediabox.width
        )

        height = float(
            page.mediabox.height
        )

        # ----------------------------------------------------
        # Оригинальная этикетка
        # ----------------------------------------------------

        writer.add_page(
            page
        )

        # ----------------------------------------------------
        # Информация
        # ----------------------------------------------------

        info_pdf = create_info_page(
            width,
            height,
            posting
        )

        info_reader = PdfReader(
            info_pdf
        )

        writer.add_page(
            info_reader.pages[0]
        )

        # ----------------------------------------------------
        # Маппинг
        # ----------------------------------------------------

        mapping_rows.append({

            "№":
                index,

            "Страница PDF":
                index,

            "Отправление":
                posting[
                    "posting_number"
                ],

            "Артикул":
                posting[
                    "article"
                ],

            "Товар":
                posting[
                    "product"
                ],

            "Кол-во":
                posting[
                    "quantity"
                ],

            "Статус":
                posting[
                    "status"
                ],

            "Этикетка":
                "✅",

            "Источник":
                label[
                    "source"
                ]
        })

    output = io.BytesIO()

    writer.write(
        output
    )

    output.seek(0)

    return (
        output,
        mapping_rows
    )


# ============================================================
# INTERFACE
# ============================================================

st.title(
    "🏷️ Ozon FBS — Отправления → Этикетки → Маппинг"
)

st.markdown(
    """
    **Скрипт полностью работает через Ozon API.**

    PDF этикеток загружать вручную не нужно.
    """
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
    "📅 Период"
)

default_to = datetime.now()

default_from = (
    default_to -
    timedelta(days=7)
)

date_from = st.sidebar.date_input(
    "От",
    value=default_from.date()
)

date_to = st.sidebar.date_input(
    "До",
    value=default_to.date()
)


# ============================================================
# КНОПКА
# ============================================================

run = st.button(
    "🚀 Получить отправления и этикетки",
    type="primary",
    use_container_width=True
)


if run:

    if not client_id:

        st.error(
            "Введите Client-Id."
        )

        st.stop()

    if not api_key:

        st.error(
            "Введите Api-Key."
        )

        st.stop()


    # ========================================================
    # ДАТЫ
    # ========================================================

    date_from_str = (
        datetime.combine(
            date_from,
            datetime.min.time()
        ).strftime(
            "%Y-%m-%dT00:00:00Z"
        )
    )

    date_to_str = (
        datetime.combine(
            date_to,
            datetime.max.time()
        ).strftime(
            "%Y-%m-%dT23:59:59Z"
        )
    )


    # ========================================================
    # ЭТАП 1
    # ========================================================

    st.header(
        "1️⃣ Получение отправлений"
    )

    with st.spinner(
        "Запрашиваем отправления Ozon..."
    ):

        try:

            raw_postings = get_postings(
                client_id,
                api_key,
                date_from_str,
                date_to_str
            )

        except Exception as e:

            st.error(
                str(e)
            )

            st.stop()


    postings = []

    for raw in raw_postings:

        posting = convert_posting(
            raw
        )

        if posting[
            "posting_number"
        ]:

            postings.append(
                posting
            )


    postings_map = {

        x[
            "posting_number"
        ]:
        x

        for x in postings
    }


    st.success(
        f"Получено отправлений: "
        f"**{len(postings)}**"
    )


    # ========================================================
    # ТАБЛИЦА ОТПРАВЛЕНИЙ
    # ========================================================

    with st.expander(
        "📦 Показать отправления"
    ):

        postings_table = [

            {
                "№": i,
                "Отправление":
                    x[
                        "posting_number"
                    ],
                "Артикул":
                    x[
                        "article"
                    ],
                "Товар":
                    x[
                        "product"
                    ],
                "Кол-во":
                    x[
                        "quantity"
                    ],
                "Статус":
                    x[
                        "status"
                    ]
            }

            for i, x in enumerate(
                postings,
                start=1
            )
        ]

        st.dataframe(
            postings_table,
            use_container_width=True,
            hide_index=True
        )


    if not postings:

        st.warning(
            "Отправлений за выбранный период нет."
        )

        st.stop()


    # ========================================================
    # ЭТАП 2
    # ========================================================

    st.header(
        "2️⃣ Получение этикеток"
    )

    posting_numbers = [

        x[
            "posting_number"
        ]

        for x in postings
    ]


    progress = st.progress(
        0,
        text="Запрашиваем этикетки..."
    )


    def update_progress(
        value,
        text
    ):

        progress.progress(
            value,
            text=text
        )


    with st.spinner(
        "Получаем PDF этикеток Ozon..."
    ):

        label_records = (
            get_labels_for_postings(
                client_id,
                api_key,
                posting_numbers,
                update_progress
            )
        )


    progress.progress(
        1,
        text="Получение этикеток завершено"
    )


    # ========================================================
    # СТАТИСТИКА
    # ========================================================

    labels_count = len(
        label_records
    )

    missing_count = (
        len(postings)
        -
        labels_count
    )


    c1, c2, c3 = st.columns(3)

    c1.metric(
        "Отправлений",
        len(postings)
    )

    c2.metric(
        "Этикеток получено",
        labels_count
    )

    c3.metric(
        "Без этикетки",
        max(
            missing_count,
            0
        )
    )


    # ========================================================
    # ЭТАП 3
    # ========================================================

    st.header(
        "3️⃣ Таблица маппинга"
    )


    label_postings = {

        x[
            "posting_number"
        ]

        for x in label_records
    }


    mapping_preview = []

    for index, posting in enumerate(
        postings,
        start=1
    ):

        posting_number = (
            posting[
                "posting_number"
            ]
        )

        has_label = (
            posting_number
            in
            label_postings
        )

        mapping_preview.append({

            "№":
                index,

            "Отправление":
                posting_number,

            "Артикул":
                posting[
                    "article"
                ],

            "Товар":
                posting[
                    "product"
                ],

            "Кол-во":
                posting[
                    "quantity"
                ],

            "Статус":
                posting[
                    "status"
                ],

            "Этикетка":
                "✅"
                if has_label
                else
                "❌"
        })


    st.dataframe(
        mapping_preview,
        use_container_width=True,
        hide_index=True
    )


    # ========================================================
    # НЕПОЛУЧЕННЫЕ ЭТИКЕТКИ
    # ========================================================

    missing_labels = [

        x

        for x in postings

        if x[
            "posting_number"
        ]
        not in label_postings
    ]


    if missing_labels:

        st.warning(
            f"Не удалось получить "
            f"этикетки для "
            f"{len(missing_labels)} "
            f"отправлений."
        )

        st.dataframe(
            [
                {
                    "Отправление":
                        x[
                            "posting_number"
                        ],

                    "Артикул":
                        x[
                            "article"
                        ],

                    "Товар":
                        x[
                            "product"
                        ],

                    "Статус":
                        x[
                            "status"
                        ]
                }

                for x in missing_labels
            ],
            use_container_width=True,
            hide_index=True
        )


    # ========================================================
    # ЭТАП 4
    # ========================================================

    if label_records:

        st.header(
            "4️⃣ Формирование итогового PDF"
        )

        try:

            final_pdf, mapping_rows = (
                create_result_pdf(
                    label_records,
                    postings_map
                )
            )

            st.success(
                f"Итоговый PDF готов. "
                f"Этикеток внутри: "
                f"**{len(mapping_rows)}**."
            )


            # ------------------------------------------------
            # ФИНАЛЬНАЯ ТАБЛИЦА
            # ------------------------------------------------

            with st.expander(
                "📋 Финальный маппинг PDF"
            ):

                st.dataframe(
                    mapping_rows,
                    use_container_width=True,
                    hide_index=True
                )


            # ------------------------------------------------
            # DOWNLOAD
            # ------------------------------------------------

            st.download_button(
                label=(
                    "⬇️ Скачать "
                    "Ozon_FBS_Labels_Mapped.pdf"
                ),

                data=(
                    final_pdf.getvalue()
                ),

                file_name=(
                    "Ozon_FBS_Labels_Mapped.pdf"
                ),

                mime="application/pdf",

                type="primary",

                use_container_width=True
            )


        except Exception as e:

            st.error(
                "Ошибка формирования PDF:"
            )

            st.exception(e)

    else:

        st.error(
            "Ozon не вернул ни одной этикетки."
        )
        
