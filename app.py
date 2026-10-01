import streamlit as st
import re
import os
import requests

from io import BytesIO
from datetime import datetime, timedelta, timezone

from pypdf import PdfReader, PdfWriter

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# НАСТРОЙКИ СТРАНИЦЫ
# ============================================================

st.set_page_config(
    page_title="Ozon FBS — Этикетки + API",
    page_icon="🖨️",
    layout="wide"
)

st.title(
    "🖨️ Ozon FBS — Умные этикетки через API"
)

st.write(
    "Загружаются только этикетки. "
    "Артикул, товар и количество автоматически "
    "получаются из Ozon API."
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

        response = requests.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        with open(
            font_path,
            "wb"
        ) as f:

            f.write(
                response.content
            )

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

def normalize_shipment(value):

    if not value:

        return ""

    value = str(
        value
    ).strip()


    # Убираем пробелы
    value = re.sub(
        r"\s+",
        "",
        value
    )


    # Нормализация похожих символов
    value = (
        value
        .replace("І", "I")
        .replace("і", "i")
    )


    return value.lower()


# ============================================================
# ИЗВЛЕЧЕНИЕ НОМЕРА ОТПРАВЛЕНИЯ С ЭТИКЕТКИ
# ============================================================

def extract_shipment_from_label(page):

    try:

        text = page.extract_text()

    except Exception:

        text = None


    if not text:

        return None


    # ========================================================
    # Основной формат:
    #
    # 78277691-0407-1
    # ========================================================

    patterns = [

        r"\d{8,15}-\d{4}-\d+",

        r"\d{7,15}\s*-\s*\d{3,5}\s*-\s*\d+"
    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            text
        )


        if match:

            value = re.sub(
                r"\s+",
                "",
                match.group(0)
            )


            return normalize_shipment(
                value
            )


    # ========================================================
    # Иногда текст между символами разбит
    # ========================================================

    clean = re.sub(
        r"\s+",
        "",
        text
    )


    match = re.search(
        r"\d{8,15}-\d{4}-\d+",
        clean
    )


    if match:

        return normalize_shipment(
            match.group(0)
        )


    # ========================================================
    # Дополнительный формат ii...
    # ========================================================

    match = re.search(
        r"ii\d{8,20}",
        clean,
        flags=re.IGNORECASE
    )


    if match:

        return normalize_shipment(
            match.group(0)
        )


    return None


# ============================================================
# ПОСТРОЕНИЕ КЛЮЧА ИЗ POSTING NUMBER
# ============================================================

def normalize_api_posting(posting_number):

    if not posting_number:

        return ""

    return normalize_shipment(
        posting_number
    )


# ============================================================
# API OZON
# ============================================================

def get_assembly_data_from_api(
    client_id,
    api_key,
    days_back=30,
    days_forward=7
):

    url = (
        "https://api-seller.ozon.ru/"
        "v1/assembly/fbs/posting/list"
    )


    headers = {

        "Client-Id":
            str(client_id).strip(),

        "Api-Key":
            str(api_key).strip(),

        "Content-Type":
            "application/json"
    }


    # ========================================================
    # Время в UTC
    # ========================================================

    now = datetime.now(
        timezone.utc
    )


    cutoff_from = (
        now
        -
        timedelta(
            days=days_back
        )
    ).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


    cutoff_to = (
        now
        +
        timedelta(
            days=days_forward
        )
    ).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


    data_mapping = {}


    cursor = ""


    total_api_records = 0

    pages = 0


    while True:

        pages += 1


        payload = {

            "filter": {

                "cutoff_from":
                    cutoff_from,

                "cutoff_to":
                    cutoff_to
            },

            "limit":
                1000,

            "sort_dir":
                "ASC"
        }


        if cursor:

            payload[
                "cursor"
            ] = cursor


        try:

            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=60
            )

        except requests.RequestException as e:

            raise Exception(
                f"Ошибка соединения с Ozon API: {e}"
            )


        # ====================================================
        # HTTP ошибка
        # ====================================================

        if response.status_code != 200:

            error_text = response.text

            try:

                error_json = response.json()

                error_text = str(
                    error_json
                )

            except Exception:

                pass


            raise Exception(
                f"Ozon API HTTP "
                f"{response.status_code}: "
                f"{error_text}"
            )


        # ====================================================
        # JSON
        # ====================================================

        try:

            result = response.json()

        except Exception:

            raise Exception(
                "Ozon API вернул некорректный JSON."
            )


        postings = result.get(
            "postings",
            []
        )


        total_api_records += len(
            postings
        )


        # ====================================================
        # Обработка отправлений
        # ====================================================

        for posting in postings:

            posting_number = posting.get(
                "posting_number"
            )


            if not posting_number:

                continue


            key = normalize_api_posting(
                posting_number
            )


            if not key:

                continue


            products = posting.get(
                "products",
                []
            )


            names = []

            articles = []

            quantities = []


            total_qty = 0


            # ==================================================
            # Товары внутри отправления
            # ==================================================

            for product in products:

                product_name = str(
                    product.get(
                        "product_name",
                        ""
                    )
                    or ""
                ).strip()


                offer_id = str(
                    product.get(
                        "offer_id",
                        ""
                    )
                    or ""
                ).strip()


                quantity_raw = product.get(
                    "quantity",
                    0
                )


                try:

                    quantity = int(
                        quantity_raw
                    )

                except Exception:

                    try:

                        quantity = int(
                            float(
                                quantity_raw
                            )
                        )

                    except Exception:

                        quantity = 0


                if product_name:

                    names.append(
                        product_name
                    )


                if offer_id:

                    articles.append(
                        offer_id
                    )


                quantities.append(
                    quantity
                )


                total_qty += quantity


            # ==================================================
            # Если products пустой
            # ==================================================

            if not products:

                names = [
                    str(
                        posting.get(
                            "product_name",
                            ""
                        )
                        or ""
                    )
                ]


            data_mapping[key] = {

                "shipment":
                    posting_number,

                "article":
                    " + ".join(
                        articles
                    )
                    if articles
                    else "-",

                "qty":
                    str(
                        total_qty
                    ),

                "name":
                    " + ".join(
                        names
                    )
                    if names
                    else "Товар",

                "label":
                    "-"
            }


        # ====================================================
        # Пагинация
        # ====================================================

        next_cursor = result.get(
            "cursor"
        )


        if not next_cursor:

            break


        # защита от зацикливания
        if next_cursor == cursor:

            break


        cursor = next_cursor


        # Дополнительная защита
        if pages > 1000:

            break


    return (
        data_mapping,
        {
            "from":
                cutoff_from,

            "to":
                cutoff_to,

            "pages":
                pages,

            "api_records":
                total_api_records
        }
    )


# ============================================================
# ПОПЫТКА НАЙТИ ОТПРАВЛЕНИЕ С ПОМОЩЬЮ НЕСКОЛЬКИХ ВАРИАНТОВ
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


    # Прямое совпадение
    if normalized in api_mapping:

        return api_mapping[
            normalized
        ]


    # ========================================================
    # Иногда проблема в пробелах / дефисах
    # ========================================================

    compact = re.sub(
        r"[^0-9a-zа-я]",
        "",
        normalized
    )


    for key, value in api_mapping.items():

        key_compact = re.sub(
            r"[^0-9a-zа-я]",
            "",
            key
        )


        if key_compact == compact:

            return value


    return None


# ============================================================
# ПЕРЕНОС ТЕКСТА НА СТРАНИЦУ
# ============================================================

def split_text(
    text,
    chars
):

    if not text:

        return []


    words = str(
        text
    ).split()


    result = []

    current = ""


    for word in words:

        test = (
            current
            +
            word
            +
            " "
        )


        if len(test) <= chars:

            current = test

        else:

            if current:

                result.append(
                    current.strip()
                )


            current = (
                word
                +
                " "
            )


    if current:

        result.append(
            current.strip()
        )


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

    packet = BytesIO()


    c = canvas.Canvas(
        packet,
        pagesize=(
            width,
            height
        )
    )


    margin = 10


    # ========================================================
    # ЗАГОЛОВОК
    # ========================================================

    c.setFont(
        font_name,
        9
    )


    c.drawString(
        margin,
        height - 16,
        f"Заказ: {order_number}"
    )


    c.line(
        margin,
        height - 20,
        width - margin,
        height - 20
    )


    # ========================================================
    # Статус
    # ========================================================

    c.setFont(
        font_name,
        8
    )


    c.drawString(
        margin,
        height - 31,
        f"Статус: {match_status}"
    )


    # ========================================================
    # Этикетка
    # ========================================================

    label = product_info.get(
        "label",
        "-"
    )


    c.setFont(
        font_name,
        9
    )


    c.drawString(
        margin,
        height - 45,
        f"Этикетка: {label}"
    )


    # ========================================================
    # Артикул
    # ========================================================

    article = product_info.get(
        "article",
        "-"
    )


    c.setFont(
        font_name,
        11
    )


    c.drawString(
        margin,
        height - 62,
        f"Арт: {article}"
    )


    # ========================================================
    # НАЗВАНИЕ ТОВАРА
    # ========================================================

    name = product_info.get(
        "name",
        "Товар не найден"
    )


    top = height - 80

    bottom = 55

    font_size = 9

    line_height = 11


    lines = split_text(
        name,
        30
    )


    while (
        len(lines)
        *
        line_height
        >
        top - bottom
        and
        font_size > 6
    ):

        font_size -= 0.5

        line_height -= 0.5


        lines = split_text(
            name,
            max(
                20,
                int(
                    30
                    *
                    9
                    /
                    font_size
                )
            )
        )


    c.setFont(
        font_name,
        font_size
    )


    y = top


    for line in lines:

        if y <= bottom:

            break


        c.drawString(
            margin,
            y,
            line
        )


        y -= line_height


    # ========================================================
    # КОЛИЧЕСТВО
    # ========================================================

    qty = product_info.get(
        "qty",
        "?"
    )


    c.setFont(
        font_name,
        22
    )


    c.drawString(
        margin,
        15,
        f"КОЛ-ВО: {qty}"
    )


    c.save()


    packet.seek(0)


    return PdfReader(
        packet
    ).pages[0]


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header(
    "🔑 Настройки API Ozon"
)


client_id = st.sidebar.text_input(
    "Client-Id",
    placeholder="Введите Client-Id"
)


api_key = st.sidebar.text_input(
    "Api-Key",
    type="password",
    placeholder="Введите Api-Key"
)


st.sidebar.divider()


st.sidebar.subheader(
    "📅 Период поиска"
)


days_back = st.sidebar.number_input(
    "Дней назад",
    min_value=1,
    max_value=90,
    value=30,
    step=1
)


days_forward = st.sidebar.number_input(
    "Дней вперед",
    min_value=0,
    max_value=30,
    value=7,
    step=1
)


# ============================================================
# ФАЙЛ
# ============================================================

labels_file = st.file_uploader(
    "1️⃣ Этикетки Ozon (PDF)",
    type=["pdf"]
)


st.info(
    "💡 Лист подбора загружать не нужно. "
    "Данные по отправлениям будут получены из Ozon API."
)


# ============================================================
# КНОПКА
# ============================================================

if labels_file:

    if not client_id or not api_key:

        st.warning(
            "Введите Client-Id и Api-Key "
            "в боковой панели слева."
        )

    else:

        if st.button(
            "🚀 Получить данные API и склеить",
            type="primary",
            use_container_width=True
        ):

            with st.status(
                "Запускаем обработку...",
                expanded=True
            ) as status:


                # ==================================================
                # 1. API
                # ==================================================

                status.write(
                    "🔄 Получаем отправления из Ozon API..."
                )


                try:

                    (
                        assembly_data,
                        api_info
                    ) = get_assembly_data_from_api(
                        client_id,
                        api_key,
                        days_back,
                        days_forward
                    )


                except Exception as e:

                    status.update(
                        label="❌ Ошибка Ozon API",
                        state="error"
                    )


                    st.error(
                        str(e)
                    )


                    st.stop()


                # ==================================================
                # API диагностика
                # ==================================================

                st.subheader(
                    "📊 Данные Ozon API"
                )


                api_col1, api_col2, api_col3 = st.columns(3)


                with api_col1:

                    st.metric(
                        "Отправлений",
                        len(
                            assembly_data
                        )
                    )


                with api_col2:

                    st.metric(
                        "Записей API",
                        api_info[
                            "api_records"
                        ]
                    )


                with api_col3:

                    st.metric(
                        "Страниц API",
                        api_info[
                            "pages"
                        ]
                    )


                st.caption(
                    f"Период API: "
                    f"{api_info['from']} — "
                    f"{api_info['to']}"
                )


                # ==================================================
                # API таблица
                # ==================================================

                if assembly_data:

                    api_table = []


                    for key, item in assembly_data.items():

                        api_table.append({

                            "№ отправления":
                                item[
                                    "shipment"
                                ],

                            "Артикул":
                                item[
                                    "article"
                                ],

                            "Кол-во":
                                item[
                                    "qty"
                                ],

                            "Товар":
                                item[
                                    "name"
                                ]
                        })


                    with st.expander(
                        f"🔗 Отправления из API — "
                        f"{len(api_table)}",
                        expanded=False
                    ):

                        st.dataframe(
                            api_table,
                            use_container_width=True,
                            hide_index=True
                        )


                if not assembly_data:

                    status.update(
                        label=(
                            "❌ Ozon API не вернул "
                            "ни одного отправления"
                        ),
                        state="error"
                    )


                    st.error(
                        "Проверьте период поиска, "
                        "Client-Id и Api-Key."
                    )


                    st.stop()


                status.write(
                    f"✅ Из API получено "
                    f"{len(assembly_data)} отправлений."
                )


                # ==================================================
                # 2. Читаем PDF этикеток
                # ==================================================

                status.write(
                    "📄 Разбираем PDF этикеток..."
                )


                labels_bytes = (
                    labels_file.getvalue()
                )


                try:

                    reader = PdfReader(
                        BytesIO(
                            labels_bytes
                        )
                    )

                except Exception as e:

                    status.update(
                        label="❌ Ошибка чтения PDF",
                        state="error"
                    )


                    st.error(
                        f"Не удалось открыть PDF: {e}"
                    )


                    st.stop()


                writer = PdfWriter()


                found = 0

                not_found = 0

                unknown = 0


                diagnostics = []


                # ==================================================
                # 3. Обрабатываем этикетки
                # ==================================================

                for page_number, page in enumerate(
                    reader.pages,
                    start=1
                ):


                    # ----------------------------------------------
                    # Оригинальная этикетка
                    # ----------------------------------------------

                    writer.add_page(
                        page
                    )


                    # ----------------------------------------------
                    # Номер отправления
                    # ----------------------------------------------

                    shipment = (
                        extract_shipment_from_label(
                            page
                        )
                    )


                    width = float(
                        page.mediabox.width
                    )


                    height = float(
                        page.mediabox.height
                    )


                    # =================================================
                    # Отправление распознано
                    # =================================================

                    if shipment:

                        info = find_api_posting(
                            shipment,
                            assembly_data
                        )


                        if info:

                            found += 1

                            display_number = (
                                info[
                                    "shipment"
                                ]
                            )


                            match_status = (
                                "НАЙДЕНО В API"
                            )


                        else:

                            not_found += 1

                            display_number = (
                                shipment.upper()
                            )


                            info = {

                                "label":
                                    "-",

                                "article":
                                    "-",

                                "qty":
                                    "?",

                                "name":
                                    (
                                        "ОТПРАВЛЕНИЕ "
                                        "НЕ НАЙДЕНО В API"
                                    )
                            }


                            match_status = (
                                "НЕ НАЙДЕНО В API"
                            )


                    # =================================================
                    # Отправление не распознано
                    # =================================================

                    else:

                        unknown += 1


                        display_number = (
                            "???"
                        )


                        info = {

                            "label":
                                "-",

                            "article":
                                "-",

                            "qty":
                                "-",

                            "name":
                                (
                                    "НОМЕР ОТПРАВЛЕНИЯ "
                                    "НЕ РАСПОЗНАН "
                                    "НА ЭТИКЕТКЕ"
                                )
                        }


                        match_status = (
                            "НЕ РАСПОЗНАН"
                        )


                    # =================================================
                    # Добавляем информационную страницу
                    # =================================================

                    writer.add_page(
                        create_info_label(
                            width,
                            height,
                            display_number,
                            info,
                            match_status
                        )
                    )


                    # =================================================
                    # Диагностика
                    # =================================================

                    diagnostics.append({

                        "Страница":
                            page_number,

                        "Отправление":
                            shipment
                            if shipment
                            else "—",

                        "API":
                            "Да"
                            if shipment
                            and find_api_posting(
                                shipment,
                                assembly_data
                            )
                            else "Нет"
                    })


                # ==================================================
                # ГОТОВО
                # ==================================================

                status.update(
                    label=(
                        f"Готово! "
                        f"Найдено: {found}; "
                        f"не найдено: {not_found}; "
                        f"не распознано: {unknown}"
                    ),
                    state="complete"
                )


            # ======================================================
            # ИТОГОВАЯ СТАТИСТИКА
            # ======================================================

            st.subheader(
                "📊 Результат"
            )


            col1, col2, col3, col4 = st.columns(4)


            with col1:

                st.metric(
                    "Этикеток",
                    len(
                        reader.pages
                    )
                )


            with col2:

                st.metric(
                    "Найдено в API",
                    found
                )


            with col3:

                st.metric(
                    "Нет в API",
                    not_found
                )


            with col4:

                st.metric(
                    "Не распознано",
                    unknown
                )


            # ======================================================
            # ДИАГНОСТИКА ЭТИКЕТОК
            # ======================================================

            with st.expander(
                "🔍 Диагностика этикеток",
                expanded=True
            ):

                st.dataframe(
                    diagnostics,
                    use_container_width=True,
                    hide_index=True
                )


            # ======================================================
            # НЕ НАЙДЕННЫЕ
            # ======================================================

            missing_rows = []


            for row in diagnostics:

                if (
                    row["Отправление"] != "—"
                    and
                    row["API"] == "Нет"
                ):

                    missing_rows.append(
                        row
                    )


            if missing_rows:

                with st.expander(
                    f"⚠️ Не найдено в API — "
                    f"{len(missing_rows)}",
                    expanded=True
                ):

                    st.dataframe(
                        missing_rows,
                        use_container_width=True,
                        hide_index=True
                    )


            # ======================================================
            # СОЗДАЁМ ФАЙЛ
            # ======================================================

            output = BytesIO()


            writer.write(
                output
            )


            output.seek(0)


            st.success(
                "✅ Готово! "
                "Каждая оригинальная этикетка "
                "дополнена информационной страницей."
            )


            st.download_button(
                "📥 Скачать Ready_Labels.pdf",
                output,
                "Ready_Labels.pdf",
                "application/pdf",
                use_container_width=True
            )
