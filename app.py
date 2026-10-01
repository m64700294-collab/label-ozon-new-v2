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

def register_fonts():

    paths = [
        (
            "DejaVuSans",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
        (
            "DejaVuSansBold",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        ),
    ]

    regular = "Helvetica"
    bold = "Helvetica-Bold"

    for name, path in paths:

        try:
            pdfmetrics.registerFont(
                TTFont(name, path)
            )

            if name == "DejaVuSans":
                regular = name

            if name == "DejaVuSansBold":
                bold = name

        except Exception:
            pass

    return regular, bold


FONT_REGULAR, FONT_BOLD = register_fonts()


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================

def normalize_text(value):

    if value is None:
        return ""

    value = str(value)

    value = (
        value
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("\t", " ")
        .replace("—", "-")
        .replace("–", "-")
        .replace("−", "-")
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def normalize_shipment(value):

    value = normalize_text(value)

    value = value.lower()

    value = re.sub(
        r"\s+",
        "",
        value
    )

    return value


def compact_shipment(value):

    value = normalize_shipment(value)

    return re.sub(
        r"[^0-9a-zа-яё]",
        "",
        value
    )


# ============================================================
# ПОИСК НОМЕРА НА ЭТИКЕТКЕ
# ============================================================

def find_possible_numbers(text):

    """
    Ищем максимально широкий набор вариантов.

    Важно:
    сначала ищем полноценный номер Ozon,
    потом более общие комбинации.
    """

    if not text:
        return []

    text = normalize_text(text)

    candidates = []

    patterns = [

        # ----------------------------------------------------
        # Классический Ozon:
        # 12345678-0000-1
        # ----------------------------------------------------

        r"\b\d{8,15}-\d{3,8}-\d{1,8}\b",

        # 123456789-0000-1
        r"\b\d{7,16}-\d{3,8}-\d{1,8}\b",

        # ----------------------------------------------------
        # Варианты с пробелами вокруг дефиса
        # ----------------------------------------------------

        r"\b\d{7,16}\s*-\s*\d{3,8}\s*-\s*\d{1,8}\b",

        # ----------------------------------------------------
        # Любая длинная цепочка из цифр и дефисов
        # ----------------------------------------------------

        r"\b\d{7,16}(?:\s*-\s*\d{1,10}){1,4}\b",

        # ----------------------------------------------------
        # Если PDF разделил номер пробелами
        # ----------------------------------------------------

        r"\b\d{7,16}\s+\d{3,10}\s+\d{1,10}\b",
    ]

    for pattern in patterns:

        matches = re.findall(
            pattern,
            text
        )

        for match in matches:

            candidate = normalize_shipment(
                match
            )

            candidate = re.sub(
                r"\s*-\s*",
                "-",
                candidate
            )

            if candidate not in candidates:
                candidates.append(candidate)

    return candidates


def extract_shipment_from_label(page):

    try:
        text = page.extract_text() or ""
    except Exception:
        text = ""

    if not text:
        return None, "", []


    # ========================================================
    # ВАРИАНТ 1 — ПОЛНОЦЕННЫЕ НОМЕРА
    # ========================================================

    candidates = find_possible_numbers(
        text
    )

    if candidates:

        # Предпочитаем самый длинный
        candidates_sorted = sorted(
            candidates,
            key=lambda x: (
                x.count("-"),
                len(x)
            ),
            reverse=True
        )

        return (
            candidates_sorted[0],
            text,
            candidates_sorted
        )


    # ========================================================
    # ВАРИАНТ 2 — ИЩЕМ РЯДОМ СО СЛОВАМИ
    # ========================================================

    lines = [
        x.strip()
        for x in text.splitlines()
        if x.strip()
    ]

    keywords = [
        "отправление",
        "отправлен",
        "номер",
        "заказ",
        "order",
        "posting",
        "shipment",
        "ozon"
    ]

    for i, line in enumerate(lines):

        low = line.lower()

        if any(
            keyword in low
            for keyword in keywords
        ):

            # Сама строка
            local_candidates = find_possible_numbers(
                line
            )

            if local_candidates:

                return (
                    local_candidates[0],
                    text,
                    local_candidates
                )

            # Следующая строка
            if i + 1 < len(lines):

                local_candidates = find_possible_numbers(
                    lines[i + 1]
                )

                if local_candidates:

                    return (
                        local_candidates[0],
                        text,
                        local_candidates
                    )


    # ========================================================
    # ВАРИАНТ 3 — ДЛИННЫЕ ЧИСЛА
    # ========================================================

    long_numbers = re.findall(
        r"\b\d{10,18}\b",
        text
    )

    if long_numbers:

        # Убираем очевидные служебные номера
        filtered = []

        for number in long_numbers:

            if len(number) < 10:
                continue

            filtered.append(
                number
            )

        if filtered:

            # Сохраняем кандидатов,
            # но НЕ считаем это надёжным
            return (
                filtered[0],
                text,
                filtered
            )


    return None, text, []


# ============================================================
# API OZON
# ============================================================

API_URL = (
    "https://api-seller.ozon.ru/"
    "v1/assembly/fbs/posting/list"
)


def api_request(
    client_id,
    api_key,
    date_from,
    date_to,
    limit=1000
):

    headers = {
        "Client-Id": str(client_id).strip(),
        "Api-Key": str(api_key).strip(),
        "Content-Type": "application/json"
    }

    postings_all = []

    cursor = ""

    while True:

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

        response = requests.post(
            API_URL,
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
                f"Ozon API HTTP "
                f"{response.status_code}: {error}"
            )

        data = response.json()

        postings = data.get(
            "postings",
            []
        )

        if not postings:
            break

        postings_all.extend(
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

        if len(postings) < limit:
            break

    return postings_all


# ============================================================
# POSTING → RECORD
# ============================================================

def posting_to_record(posting):

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

        if not isinstance(
            product,
            dict
        ):
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
            names.append(
                str(name)
            )

        if article:
            articles.append(
                str(article)
            )

        try:
            quantities.append(
                float(quantity)
            )
        except Exception:
            pass

    total_qty = sum(
        quantities
    )

    if total_qty.is_integer():
        total_qty = int(total_qty)

    return {
        "shipment": str(
            posting_number
        ),
        "article": (
            " + ".join(articles)
            if articles
            else "-"
        ),
        "name": (
            " + ".join(names)
            if names
            else "-"
        ),
        "qty": str(
            total_qty
        ),
        "raw": posting
    }


# ============================================================
# ДОБАВЛЕНИЕ В MAP
# ============================================================

def add_postings_to_mapping(
    postings,
    api_mapping
):

    for posting in postings:

        record = posting_to_record(
            posting
        )

        shipment = record[
            "shipment"
        ]

        if not shipment:
            continue

        normalized = normalize_shipment(
            shipment
        )

        if not normalized:
            continue

        api_mapping[
            normalized
        ] = record

        compact = compact_shipment(
            normalized
        )

        if compact:

            api_mapping[
                f"__compact__{compact}"
            ] = record


# ============================================================
# API КУСКАМИ
# ============================================================

def get_assembly_data_chunked(
    client_id,
    api_key,
    days_back=180,
    days_forward=7,
    chunk_days=30
):

    api_mapping = {}

    now = datetime.utcnow()

    start = (
        now -
        timedelta(
            days=days_back
        )
    )

    finish = (
        now +
        timedelta(
            days=days_forward
        )
    )

    current = start

    chunks = max(
        1,
        int(
            (
                finish - start
            ).total_seconds()
            /
            (
                chunk_days *
                86400
            )
        ) + 1
    )

    progress = st.progress(
        0,
        text="Получение данных API..."
    )

    request_number = 0
    total_postings = 0

    while current < finish:

        chunk_end = min(
            current +
            timedelta(
                days=chunk_days
            ),
            finish
        )

        date_from = current.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        date_to = chunk_end.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        request_number += 1

        postings = api_request(
            client_id,
            api_key,
            date_from,
            date_to
        )

        total_postings += len(
            postings
        )

        add_postings_to_mapping(
            postings,
            api_mapping
        )

        progress.progress(
            min(
                request_number / chunks,
                1
            ),
            text=(
                f"Запрос {request_number}: "
                f"{date_from[:10]} → "
                f"{date_to[:10]} | "
                f"получено {len(postings)}"
            )
        )

        current = chunk_end

    progress.progress(
        1,
        text="API завершено"
    )

    return (
        api_mapping,
        total_postings,
        request_number
    )


# ============================================================
# ПОИСК API
# ============================================================

def find_api_posting(
    shipment,
    api_mapping
):

    if not shipment:
        return None

    normalized = normalize_shipment(
        shipment
    )

    if normalized in api_mapping:
        return api_mapping[
            normalized
        ]

    compact = compact_shipment(
        normalized
    )

    if compact:

        key = (
            "__compact__"
            + compact
        )

        if key in api_mapping:
            return api_mapping[
                key
            ]

    return None


# ============================================================
# TEXT WRAP
# ============================================================

def split_text(
    text,
    chars=45
):

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

        text = text[
            pos:
        ].strip()

    if text:
        result.append(
            text
        )

    return result


# ============================================================
# INFO PDF
# ============================================================

def create_info_label(
    width,
    height,
    order_number,
    product_info,
    status="OK"
):

    buffer = io.BytesIO()

    c = canvas.Canvas(
        buffer,
        pagesize=(
            width,
            height
        )
    )

    title = (
        "ИНФОРМАЦИЯ ОТПРАВЛЕНИЯ"
        if status == "OK"
        else
        "ОТПРАВЛЕНИЕ НЕ НАЙДЕНО"
    )

    c.setFont(
        FONT_BOLD,
        16
    )

    c.drawCentredString(
        width / 2,
        height - 40,
        title
    )

    y = height - 85

    c.setFont(
        FONT_BOLD,
        10
    )

    c.drawString(
        30,
        y,
        "Отправление:"
    )

    c.setFont(
        FONT_REGULAR,
        10
    )

    c.drawString(
        125,
        y,
        str(
            order_number or "—"
        )
    )

    y -= 30

    c.setFont(
        FONT_BOLD,
        10
    )

    c.drawString(
        30,
        y,
        "Статус:"
    )

    c.setFont(
        FONT_REGULAR,
        10
    )

    c.drawString(
        125,
        y,
        (
            "Найдено в Ozon API"
            if status == "OK"
            else
            "Не найдено в Ozon API"
        )
    )

    y -= 35

    # Артикул
    c.setFont(
        FONT_BOLD,
        10
    )

    c.drawString(
        30,
        y,
        "Артикул:"
    )

    y -= 16

    c.setFont(
        FONT_REGULAR,
        9
    )

    for line in split_text(
        product_info.get(
            "article",
            "-"
        ),
        55
    ):

        c.drawString(
            30,
            y,
            line
        )

        y -= 14

    y -= 8

    # Товар
    c.setFont(
        FONT_BOLD,
        10
    )

    c.drawString(
        30,
        y,
        "Товар:"
    )

    y -= 16

    c.setFont(
        FONT_REGULAR,
        9
    )

    for line in split_text(
        product_info.get(
            "name",
            "-"
        ),
        55
    ):

        c.drawString(
            30,
            y,
            line
        )

        y -= 14

    y -= 8

    c.setFont(
        FONT_BOLD,
        10
    )

    c.drawString(
        30,
        y,
        "Количество:"
    )

    c.setFont(
        FONT_REGULAR,
        10
    )

    c.drawString(
        125,
        y,
        str(
            product_info.get(
                "qty",
                "-"
            )
        )
    )

    c.showPage()
    c.save()

    buffer.seek(0)

    return buffer


# ============================================================
# СОЗДАНИЕ PDF
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

    diagnostics = []

    found = 0
    not_found = 0

    for record in label_records:

        page_number = record[
            "page"
        ]

        page = reader.pages[
            page_number - 1
        ]

        shipment = record[
            "shipment"
        ]

        api_record = find_api_posting(
            shipment,
            api_mapping
        )

        if api_record:

            found += 1

            status = "OK"

            info = api_record

        else:

            not_found += 1

            status = "НЕ НАЙДЕНО"

            info = {
                "article": "-",
                "name": "-",
                "qty": "-"
            }

        writer.add_page(
            page
        )

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
            "Отправление": (
                shipment
                or "НЕ РАСПОЗНАНО"
            ),
            "Кандидаты": " | ".join(
                record.get(
                    "candidates",
                    []
                )
            ),
            "Артикул": (
                info.get(
                    "article",
                    "-"
                )
                if api_record
                else "-"
            ),
            "Товар": (
                info.get(
                    "name",
                    "-"
                )
                if api_record
                else "-"
            ),
            "Кол-во": (
                info.get(
                    "qty",
                    "-"
                )
                if api_record
                else "-"
            ),
            "API": status
        })

    output = io.BytesIO()

    writer.write(
        output
    )

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
    "Распознавание номера отправления "
    "из PDF + поиск отправления через Ozon API."
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


# ============================================================
# PDF
# ============================================================

uploaded_file = st.file_uploader(
    "📄 Загрузите PDF этикеток",
    type=["pdf"]
)


if uploaded_file:

    try:

        reader = PdfReader(
            uploaded_file
        )

    except Exception as e:

        st.error(
            f"Ошибка открытия PDF: {e}"
        )

        st.stop()

    total_pages = len(
        reader.pages
    )

    st.info(
        f"Страниц в PDF: **{total_pages}**"
    )


    # ========================================================
    # РАСПОЗНАВАНИЕ
    # ========================================================

    st.subheader(
        "1️⃣ Распознавание этикеток"
    )

    label_records = []

    progress = st.progress(
        0
    )

    for page_number, page in enumerate(
        reader.pages,
        start=1
    ):

        shipment, raw_text, candidates = (
            extract_shipment_from_label(
                page
            )
        )

        label_records.append({
            "page": page_number,
            "shipment": shipment,
            "raw_text": raw_text,
            "candidates": candidates
        })

        progress.progress(
            page_number / total_pages,
            text=(
                f"Этикетка "
                f"{page_number} из "
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

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "Этикеток",
        total_pages
    )

    c2.metric(
        "Номер распознан",
        recognized
    )

    c3.metric(
        "Не распознан",
        not_recognized
    )


    # ========================================================
    # ДИАГНОСТИКА PDF
    # ========================================================

    if not_recognized > 0:

        st.error(
            f"❌ Не удалось определить "
            f"номер на {not_recognized} "
            f"этикетках."
        )

        with st.expander(
            "🔍 Показать текст первых 5 нераспознанных этикеток"
        ):

            shown = 0

            for record in label_records:

                if record["shipment"]:
                    continue

                st.markdown(
                    f"### Страница {record['page']}"
                )

                text_preview = record[
                    "raw_text"
                ]

                if not text_preview:
                    text_preview = (
                        "[PDF не содержит извлекаемого текста]"
                    )

                st.code(
                    text_preview[:5000]
                )

                shown += 1

                if shown >= 5:
                    break


        with st.expander(
            "🔍 Показать найденные кандидаты"
        ):

            candidate_rows = []

            for record in label_records:

                candidate_rows.append({
                    "Страница": record[
                        "page"
                    ],
                    "Номер": record[
                        "shipment"
                    ] or "—",
                    "Кандидаты": " | ".join(
                        record[
                            "candidates"
                        ]
                    ) or "—"
                })

            st.dataframe(
                candidate_rows,
                use_container_width=True,
                hide_index=True
            )


    # ========================================================
    # API
    # ========================================================

    if not client_id or not api_key:

        st.warning(
            "Введите Client-Id и Api-Key."
        )

        st.stop()


    st.subheader(
        "2️⃣ Получение данных Ozon API"
    )

    start = st.button(
        "🚀 Получить API и собрать PDF",
        type="primary",
        use_container_width=True
    )


    if start:

        try:

            # ------------------------------------------------
            # API
            # ------------------------------------------------

            api_mapping, total_postings, requests_count = (
                get_assembly_data_chunked(
                    client_id,
                    api_key,
                    int(days_back),
                    int(days_forward),
                    30
                )
            )

            unique_postings = len([
                key
                for key in api_mapping
                if not key.startswith(
                    "__compact__"
                )
            ])

            st.success(
                f"Получено API: "
                f"**{total_postings}** записей. "
                f"Уникальных отправлений: "
                f"**{unique_postings}**."
            )


            # ------------------------------------------------
            # Сопоставление
            # ------------------------------------------------

            st.subheader(
                "3️⃣ Сопоставление"
            )

            preliminary_found = 0

            preliminary_not_found = 0

            for record in label_records:

                shipment = record[
                    "shipment"
                ]

                if not shipment:

                    preliminary_not_found += 1
                    continue

                result = find_api_posting(
                    shipment,
                    api_mapping
                )

                if result:
                    preliminary_found += 1
                else:
                    preliminary_not_found += 1


            c1, c2, c3 = st.columns(3)

            c1.metric(
                "Этикеток",
                total_pages
            )

            c2.metric(
                "Найдено в API",
                preliminary_found
            )

            c3.metric(
                "Не найдено",
                preliminary_not_found
            )


            # ------------------------------------------------
            # PDF
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
            # НЕ НАЙДЕННЫЕ
            # ------------------------------------------------

            missing = [
                x
                for x in diagnostics
                if x["API"] != "OK"
            ]

            if missing:

                st.warning(
                    f"Осталось не найдено: "
                    f"**{len(missing)}**"
                )

                st.dataframe(
                    missing,
                    use_container_width=True,
                    hide_index=True
                )

            else:

                st.success(
                    "🎉 Все этикетки "
                    "сопоставлены с API."
                )


            # ------------------------------------------------
            # ВСЕ
            # ------------------------------------------------

            with st.expander(
                "📋 Все сопоставления"
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
                "⬇️ Скачать Ready_Labels.pdf",
                data=final_pdf.getvalue(),
                file_name="Ready_Labels.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True
            )


        except Exception as e:

            st.error(
                "❌ Ошибка:"
            )

            st.exception(e)
