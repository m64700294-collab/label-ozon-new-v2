import streamlit as st
import re
import os
from io import BytesIO
from pypdf import PdfReader, PdfWriter
import pdfplumber
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import requests

### ============================================================
### НАСТРОЙКИ
### ============================================================
st.set_page_config(
    page_title="Умная склейка этикеток Ozon",
    page_icon="🖨️",
    layout="wide"
)

st.title("🖨️ Склейка: Этикетки + Лист подбора")
st.write(
    "Сервис нарезает лист подбора по слоям и сопоставляет "
    "товары с этикетками Ozon."
)

### ============================================================
### ШРИФТ
### ============================================================
@st.cache_resource
def load_font():
    font_path = "Roboto_Full_Final.ttf"
    
    if not os.path.exists(font_path):
        url = (
            "https://cdnjs.cloudflare.com/ajax/libs/"
            "pdfmake/0.1.66/fonts/Roboto/Roboto-Regular.ttf"
        )
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            with open(font_path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            st.error(f"Не удалось загрузить шрифт: {e}")
            raise

    pdfmetrics.registerFont(
        TTFont("OzonFont", font_path)
    )
    return "OzonFont"

font_name = load_font()

### ============================================================
### НОРМАЛИЗАЦИЯ НОМЕРОВ
### ============================================================
def normalize_order(order):
    """
    Нормализация номера отправления.
    """
    if not order:
        return ""
    return (
        str(order)
        .strip()
        .lower()
        .replace("і", "i")
        .replace("І", "i")
    )

def get_short_code(order):
    """
    Возвращает последние 4 цифры/символа первой части номера.
    """
    order_norm = normalize_order(order)
    if "-" in order_norm:
        first_part = order_norm.split("-")[0]
        return first_part[-4:]
    return order_norm[-4:]

def get_numeric_key(order):
    """
    Только цифры из номера.
    """
    order_norm = normalize_order(order)
    return re.sub(r"\D", "", order_norm)

### ============================================================
### ПОИСК НОМЕРОВ ОТПРАВЛЕНИЙ
### ============================================================
ORDER_PATTERN = re.compile(
    r"(\d{8,15}-\d{4}-\d+|[a-zA-Z]{0,4}\d{10,15})",
    re.IGNORECASE
)

def find_orders(text):
    if not text:
        return []
    return ORDER_PATTERN.findall(text)

### ============================================================
### УДАЛЕНИЕ НОМЕРА ОТПРАВЛЕНИЯ И ЕГО ХВОСТА
### ============================================================
def remove_order_numbers(text, orders):
    if not text:
        return ""
    result = text
    for order in orders:
        order_norm = normalize_order(order)
        result = re.sub(re.escape(order_norm), " ", result, flags=re.IGNORECASE)
        result = re.sub(re.escape(str(order)), " ", result, flags=re.IGNORECASE)
        
        short_code = get_short_code(order)
        if short_code:
            result = re.sub(rf"(?<!\d){re.escape(short_code)}(?!\d)", " ", result)
    return result

### ============================================================
### ОЧИСТКА ТЕКСТА
### ============================================================
def clean_assembly_text(text, orders):
    if not text:
        return ""
    result = text
    
    result = remove_order_numbers(result, orders)
    
    headers = [
        r"Склад МСК ООО.*?", r"Склад:.*?", r"Служба доставки:.*?",
        r"Номер отправления", r"Номер с этикетки", r"Количество отправлений",
        r"Дата:", r"Фото", r"Товар", r"Артикул", r"Кол-во", r"Этикетка",
        r"Ozon", r"Проверьте список.*?отменять их\.", r"№"
    ]
    
    for pattern in headers:
        result = re.sub(pattern, " ", result, flags=re.IGNORECASE)
        
    result = result.replace("|", " ")
    result = re.sub(r"(?m)^\s*(?:\d+\s+)+", " ", result)
    result = re.sub(r"[ \t]+", " ", result)
    return result.strip()

### ============================================================
### ПОИСК АРТИКУЛА И КОЛИЧЕСТВА
### ============================================================
def extract_article_qty(text, forbidden_codes=None):
    """
    Определяет артикул и количество даже внутри слитных строк.
    """
    if not text:
        return "-", "1"

    forbidden_codes = forbidden_codes or set()
    raw_lines = text.splitlines()
    lines = []

    for line in raw_lines:
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)

    cleaned_lines = []
    for line in lines:
        for code in forbidden_codes:
            if not code:
                continue
            line = re.sub(rf"(?<!\d){re.escape(code)}(?!\d)", " ", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            cleaned_lines.append(line)
            
    lines = cleaned_lines
    article_candidates = []
    
    # Регулярка для "правильного" артикула и список единиц измерения (чтобы не цеплять "мл", "шт")
    article_pattern = re.compile(r"^[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_\-/.]*$")
    units = {"мл", "шт", "г", "кг", "л", "mm", "cm", "m", "ml", "kg", "g", "штук", "уп", "шт."}

    for i, line in enumerate(lines):
        # 1. Ищем комбинации "Слово Цифра" внутри любой строки. 
        # Помогает, когда артикул и кол-во приклеились к названию товара.
        matches = re.finditer(r"(?:^|\s)([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_\-/.]*)\s+(\d{1,3})(?=\s|$)", line)
        for m in matches:
            art = m.group(1)
            qty = m.group(2)
            if len(art) >= 2 and not art.isdigit() and art not in forbidden_codes and art.lower() not in units:
                article_candidates.append((art, qty))

        # 2. Вариант, когда Артикул на одной строке, а Количество на следующей
        if i + 1 < len(lines):
            current = lines[i].split()[-1] if lines[i] else ""
            next_line = lines[i + 1]
            if article_pattern.fullmatch(current):
                if re.fullmatch(r"\d{1,3}", next_line) and current not in forbidden_codes:
                    if len(current) >= 2 and not current.isdigit() and current.lower() not in units:
                        article_candidates.append((current, next_line))

    # Выбираем кандидата (последнего найденного, так как артикул обычно в конце)
    if article_candidates:
        for article, qty in reversed(article_candidates):
            if re.search(r"[A-Za-zА-Яа-я_]", article) and qty not in forbidden_codes:
                return article, qty
        article, qty = article_candidates[-1]
        if qty not in forbidden_codes:
            return article, qty

    # --------------------------------------------------------
    # FALLBACK: Ищем только артикул, количество СТРОГО 1
    # --------------------------------------------------------
    possible_articles = []
    for line in lines:
        tokens = re.findall(r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_\-/.]*", line)
        for token in tokens:
            if token in forbidden_codes or token.isdigit() or len(token) < 2 or token.lower() in units:
                continue
            possible_articles.append(token)

    article = possible_articles[-1] if possible_articles else "-"
    qty = "1" 

    return article, qty

### ============================================================
### ПАРСИНГ ЛИСТА ПОДБОРА
### ============================================================
def parse_assembly_list(pdf_file):
    data = {}
    stats = {
        "pages": 0,
        "blocks": 0,
        "orders": 0,
        "matched_blocks": 0
    }

    with pdfplumber.open(pdf_file) as pdf:
        stats["pages"] = len(pdf.pages)
        progress = st.progress(0)
        status_text = st.empty()

        for page_index, page in enumerate(pdf.pages):
            status_text.text(
                f"📄 Обработка листа подбора: "
                f"{page_index + 1}/{len(pdf.pages)}"
            )

            lines = []
            for line in page.lines:
                try:
                    width = float(line.get("width", 0))
                    if width > 30:
                        lines.append(line)
                except Exception:
                    continue

            lines.sort(key=lambda x: x.get("top", 0))

            y_coords = ([0] + [line.get("top", 0) for line in lines] + [page.height])
            y_coords = sorted(set(round(float(y), 2) for y in y_coords))

            for i in range(len(y_coords) - 1):
                top = y_coords[i]
                bottom = y_coords[i + 1]

                if bottom - top < 15:
                    continue

                stats["blocks"] += 1
                bbox = (0, top, page.width, bottom)

                try:
                    crop = page.within_bbox(bbox)
                    text = crop.extract_text(layout=True)
                except Exception:
                    continue

                if not text:
                    continue

                orders_in_slice = find_orders(text)
                if not orders_in_slice:
                    continue

                stats["orders"] += len(orders_in_slice)

                forbidden_codes = set()
                for order in orders_in_slice:
                    code = get_short_code(order)
                    if code:
                        forbidden_codes.add(code)

                text_clean = clean_assembly_text(text, orders_in_slice)
                article, qty = extract_article_qty(text_clean, forbidden_codes)

                name_text = text_clean

                if article and article != "-":
                    name_text = re.sub(re.escape(article), " ", name_text, flags=re.IGNORECASE)

                if qty:
                    name_text = re.sub(rf"(?<!\d){re.escape(str(qty))}(?!\d)", " ", name_text)

                for code in forbidden_codes:
                    name_text = re.sub(rf"(?<!\d){re.escape(code)}(?!\d)", " ", name_text)

                name_text = re.sub(r"\s+", " ", name_text).strip()

                if not name_text or len(name_text) < 2:
                    name_text = "Товар"

                item = {
                    "name": name_text,
                    "article": article,
                    "qty": qty
                }

                for order in orders_in_slice:
                    order_norm = normalize_order(order)
                    code = get_short_code(order_norm)
                    num_key = get_numeric_key(order_norm)

                    if code:
                        data[code] = item
                    if num_key:
                        data[num_key] = item
                    if len(num_key) >= 10:
                        data[num_key[-10:]] = item

                    stats["matched_blocks"] += 1

            progress.progress((page_index + 1) / len(pdf.pages))

        status_text.text(f"✅ Лист подбора обработан: {stats['pages']} стр.")

    return data, stats

### ============================================================
### СОЗДАНИЕ ИНФО-БЛОКА
### ============================================================
def create_info_label(width, height, order_number, product_info):
    packet = BytesIO()
    c = canvas.Canvas(packet, pagesize=(width, height))
    x_margin = 10

    c.setFont(font_name, 10)
    c.drawString(x_margin, height - 18, f"Заказ: {order_number}")
    c.line(x_margin, height - 20, width - x_margin, height - 20)

    c.setFont(font_name, 12)
    article = product_info.get("article", "-")
    article = str(article)

    if len(article) > 25:
        article = article[:22] + "..."
    c.drawString(x_margin, height - 35, f"Арт: {article}")

    name = product_info.get("name", "Товар не найден")
    name = str(name)
    
    top_limit = height - 52
    bottom_limit = 50
    available_h = top_limit - bottom_limit
    current_size = 10
    line_h = 12

    def get_lines(txt, chars):
        words = txt.split()
        result = []
        current = ""
        for word in words:
            if len(current) + len(word) < chars:
                current += word + " "
            else:
                if current:
                    result.append(current.strip())
                current = word + " "
        if current:
            result.append(current.strip())
        return result

    lines = get_lines(name, 32)
    while len(lines) * line_h > available_h and current_size > 6:
        current_size -= 0.5
        line_h -= 0.6
        lines = get_lines(name, int(32 * (10 / current_size)))

    c.setFont(font_name, current_size)
    y_text = top_limit
    for line in lines:
        if y_text > bottom_limit:
            c.drawString(x_margin, y_text, line)
            y_text -= line_h

    c.setFont(font_name, 24)
    qty = product_info.get("qty", "?")
    c.drawString(x_margin, 15, f"КОЛ-ВО: {qty}")
    
    c.save()
    packet.seek(0)
    return PdfReader(packet).pages[0]

### ============================================================
### ИНТЕРФЕЙС И СКЛЕЙКА
### ============================================================
col1, col2 = st.columns(2)

with col1:
    labels_file = st.file_uploader("1️⃣ Этикетки (PDF)", type="pdf")

with col2:
    assembly_file = st.file_uploader("2️⃣ Лист подбора отправлений (PDF)", type="pdf")

if labels_file and assembly_file:
    if st.button("🚀 Склеить файлы", type="primary", use_container_width=True):
        total_labels = 0
        success_count = 0
        error_orders = []

        with st.status("Анализ и склейка...", expanded=True) as status:
            st.write("🔎 Читаю лист подбора...")
            assembly_data, assembly_stats = parse_assembly_list(assembly_file)
            
            st.write(f"📄 Страниц листа подбора: {assembly_stats['pages']}")
            st.write(f"📦 Найдено отправлений: {assembly_stats['orders']}")
            st.write(f"🔑 Индексов сопоставления: {len(assembly_data)}")
            st.write("🏷 Читаю этикетки...")
            
            reader = PdfReader(labels_file)
            writer = PdfWriter()
            total_labels = len(reader.pages)

            for i in range(total_labels):
                page = reader.pages[i]
                writer.add_page(page)
                
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""

                text_no_underscores = re.sub(r"_\d+", "", text)
                clean_text = re.sub(r"\s+", "", text_no_underscores)
                clean_text_norm = clean_text.lower().replace("і", "i").replace("І", "i")

                order_match = re.search(ORDER_PATTERN, clean_text_norm)
                w = float(page.mediabox.width)
                h = float(page.mediabox.height)

                if order_match:
                    full_num = order_match.group(1)
                    short_code = get_short_code(full_num)
                    info = assembly_data.get(short_code)
                    
                    if not info:
                        num_key = get_numeric_key(full_num)
                        info = assembly_data.get(num_key)
                        if not info and len(num_key) >= 10:
                            info = assembly_data.get(num_key[-10:])

                    display_num = full_num.upper()
                    if display_num.startswith("II"):
                        display_num = "ii" + display_num[2:]

                    if not info:
                        info = {"name": "Товар не найден", "article": "-", "qty": "?"}
                        error_orders.append(display_num)
                    else:
                        forbidden_code = get_short_code(full_num)
                        current_qty = str(info.get("qty", "1"))
                        if current_qty == forbidden_code:
                            info = dict(info)
                            info["qty"] = "1"
                        success_count += 1

                    writer.add_page(create_info_label(w, h, display_num, info))
                else:
                    writer.add_page(create_info_label(w, h, "???", {"name": "Номер не распознан", "article": "-", "qty": "-"}))
                    error_orders.append("Неизвестный номер на этикетке")
                    
            status.update(label="✅ Обработка завершена!", state="complete")

        st.divider()
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Всего этикеток", total_labels)
        col_m2.metric("Успешно привязано", success_count)
        col_m3.metric("Ошибок", total_labels - success_count)

        if success_count == total_labels:
            st.success("✅ Все товары идеально сопоставлены! Можно печатать.")
        else:
            st.error(f"⚠️ Не удалось найти {total_labels - success_count} товар(ов).")
            if error_orders:
                st.write("Проблемные отправления:")
                max_errors_to_show = 100
                for err in error_orders[:max_errors_to_show]:
                    st.markdown(f"- **{err}**")
                if len(error_orders) > max_errors_to_show:
                    st.caption(f"... и ещё {len(error_orders) - max_errors_to_show}")

        output = BytesIO()
        writer.write(output)
        output.seek(0)
        
        st.download_button(
            "📥 Скачать PDF для печати",
            output,
            "Ready_Labels.pdf",
            "application/pdf",
            type="primary",
            use_container_width=True
        )
