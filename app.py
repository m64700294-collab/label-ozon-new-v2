import io
import re
import os
import urllib.request
from collections import defaultdict

import streamlit as st
import pdfplumber

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ============================================================
# НАСТРОЙКИ
# ============================================================

st.set_page_config(
    page_title="Ozon — Этикетки + Лист подбора",
    page_icon="🖨️",
    layout="wide"
)

FONT_PATH = "Roboto_Full_Final.ttf"
FONT_URL = "https://cdnjs.cloudflare.com/ajax/libs/roboto/2.138/fonts/ttf/Roboto-Regular.ttf"

# ============================================================
# ШРИФТ
# ============================================================

@st.cache_resource
def ensure_font():
    if os.path.exists(FONT_PATH):
        return FONT_PATH
    try:
        urllib.request.urlretrieve(FONT_URL, FONT_PATH)
        return FONT_PATH
    except Exception:
        return None

FONT_FILE = ensure_font()

if FONT_FILE:
    try:
        pdfmetrics.registerFont(TTFont("Roboto", FONT_FILE))
        PDF_FONT = "Roboto"
    except Exception:
        PDF_FONT = "Helvetica"
else:
    PDF_FONT = "Helvetica"

# ============================================================
# ОБЩИЕ ФУНКЦИИ
# ============================================================

def clean_spaces(value):
    if value is None: return ""
    value = str(value)
    value = value.replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()

def clean_identifier(value):
    if value is None: return ""
    value = str(value)
    value = re.sub(r"[\xa0 \n\r\t]", "", value)
    return value.lower()

def digits_only(value):
    if not value: return ""
    return re.sub(r"\D", "", str(value))

# ============================================================
# НОМЕРА ОТПРАВЛЕНИЙ И ЭТИКЕТКИ
# ============================================================

ORDER_PATTERN = re.compile(r"\d{8,15}-\d{4}-\d+", re.IGNORECASE)

def get_short_code(order):
    if not order: return ""
    order = clean_identifier(order)
    if "-" in order:
        digits = digits_only(order.split("-")[0])
        if len(digits) >= 4: return digits[-4:]
    digits = digits_only(order)
    if len(digits) >= 4: return digits[-4:]
    return ""

def get_last4(value):
    digits = digits_only(value)
    if len(digits) >= 4: return digits[-4:]
    return ""

def extract_orders_from_text(text):
    if not text: return []
    text = str(text)
    result = []
    
    # Цельный номер
    for order in ORDER_PATTERN.findall(text):
        if order not in result: result.append(order)
            
    # Разбитый номер (разрыв строки)
    flat = re.sub(r"\s+", "", text)
    for a, b in re.findall(r"(\d{8,15})(-\d{4}-\d+)", flat):
        order = a + b
        if order not in result: result.append(order)
            
    return result

def extract_ii_barcodes(text):
    if not text: return []
    result = []
    matches = re.findall(r"\bii[\s\d]{8,30}", text, flags=re.IGNORECASE)
    for value in matches:
        value = clean_identifier(value)
        match = re.match(r"(ii\d+)", value, flags=re.IGNORECASE)
        if match:
            barcode = match.group(1)
            if barcode not in result: result.append(barcode)
    return result

def extract_label_code(text):
    if not text: return ""
    codes = re.findall(r"(?<!\d)(\d{4})(?!\d)", text)
    if codes: return codes[-1]
    return ""

# ============================================================
# ГОРИЗОНТАЛЬНЫЕ ЛИНИИ И БЛОКИ
# ============================================================

def get_horizontal_blocks(page):
    lines = []
    try:
        for line in page.lines:
            x0, x1 = float(line.get("x0", 0)), float(line.get("x1", 0))
            y0, y1 = float(line.get("top", 0)), float(line.get("bottom", y0))
            width, height = abs(x1 - x0), abs(y1 - y0)
            
            if width > 30 and height < 3:
                lines.append({"top": y0, "x0": min(x0, x1), "x1": max(x0, x1), "width": width})
    except Exception:
        pass

    lines.sort(key=lambda x: x["top"])
    unique_lines = []
    
    for line in lines:
        if not unique_lines:
            unique_lines.append(line)
            continue
        if abs(line["top"] - unique_lines[-1]["top"]) < 2:
            if line["width"] > unique_lines[-1]["width"]:
                unique_lines[-1] = line
        else:
            unique_lines.append(line)

    blocks = []
    if len(unique_lines) < 2: return blocks

    for i in range(len(unique_lines) - 1):
        top, bottom = unique_lines[i]["top"], unique_lines[i + 1]["top"]
        if bottom - top < 10: continue
        blocks.append({
            "top": top,
            "bottom": bottom,
            "x0": min(unique_lines[i]["x0"], unique_lines[i + 1]["x0"]),
            "x1": max(unique_lines[i]["x1"], unique_lines[i + 1]["x1"])
        })
    return blocks

def extract_block_text(page, block):
    bbox = (block["x0"], block["top"] + 1, block["x1"], block["bottom"] - 1)
    try:
        crop = page.within_bbox(bbox)
        text = crop.extract_text(layout=True)
        return text or ""
    except Exception:
        return ""

# ============================================================
# АНАЛИЗ ТЕКСТА БЛОКА (НОВАЯ ЛОГИКА)
# ============================================================

def analyze_block_data(text, orders, label):
    """
    Извлекает Артикул, Количество и Название без привязки к заголовкам таблицы.
    """
    clean_text = text.replace("\n", " ")
    
    # 1. Формируем список запрещенных кодов, чтобы не принять их за QTY
    forbidden_codes = set()
    if label: forbidden_codes.add(label)
    for ord_num in orders:
        clean_text = clean_text.replace(ord_num, " ")
        short = get_short_code(ord_num)
        if short: forbidden_codes.add(short)
            
    for code in forbidden_codes:
        if code:
            clean_text = re.sub(rf"(?<!\d){re.escape(code)}(?!\d)", " ", clean_text)
            
    # 2. Убираем случайные заголовки таблицы, если они попали в срез
    headers = r"(Склад МСК|Склад:|Служба доставки|Дата:|Фото|Товар|Артикул|Кол-во|Этикетка|Ozon|№)"
    clean_text = re.sub(headers, " ", clean_text, flags=re.IGNORECASE)
    clean_text = clean_text.replace("|", " ")
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    
    article = "-"
    qty = "1" # По умолчанию 1, чтобы не цеплять маршрутные коды Ozon
    name = "Товар"
    
    if not clean_text:
        return name, article, qty

    # 3. Ищем паттерн в конце строки: [Артикул] [Количество]
    m = re.search(r'\s+([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_\-/.]+)\s+(\d{1,3})$', clean_text)
    if m:
        cand_article = m.group(1)
        cand_qty = m.group(2)
        if cand_article not in forbidden_codes and cand_qty not in forbidden_codes:
            article = cand_article
            qty = cand_qty
            name = clean_text[:m.start()].strip()
    else:
        # Fallback: смотрим на последние слова
        words = clean_text.split()
        if words:
            # Проверяем последнее слово на количество
            if re.fullmatch(r'\d{1,3}', words[-1]) and words[-1] not in forbidden_codes:
                qty = words[-1]
                words = words[:-1]
                
            # Проверяем новое последнее слово на артикул
            if words and len(words[-1]) >= 2 and not words[-1].isdigit():
                article = words[-1]
                words = words[:-1]
                
            name = " ".join(words).strip()
            
    if not name or len(name) < 2:
        name = "Товар"
        
    return name, article, qty

def parse_one_assembly_block(page, block):
    text = extract_block_text(page, block)
    if not text: return None

    orders = extract_orders_from_text(text)
    label = extract_label_code(text)
    if not label and orders:
        label = get_short_code(orders[0])

    # Если в блоке нет ни заказа, ни этикетки - это пустой блок или шапка таблицы
    if not orders and not label:
        return None

    # Извлекаем данные товара
    product, article, qty = analyze_block_data(text, orders, label)

    identifiers = set()
    for order in orders:
        cleaned = clean_identifier(order)
        if cleaned: identifiers.add(cleaned)
        numeric = digits_only(order)
        if numeric: identifiers.add(numeric)
        short = get_short_code(order)
        if short: identifiers.add(short)

    if label:
        identifiers.add(clean_identifier(label))

    return {
        "orders": orders,
        "article": article,
        "qty": qty,
        "product": product,
        "label": label,
        "identifiers": identifiers,
        "raw_text": text,
    }

# ============================================================
# ПАРСИНГ ЛИСТА ПОДБОРА И МАППИНГ
# ============================================================

def parse_assembly_pdf(pdf_bytes):
    records = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            blocks = get_horizontal_blocks(page)
            for block in blocks:
                record = parse_one_assembly_block(page, block)
                if record:
                    record["page"] = page_number
                    records.append(record)

    # Дедупликация
    unique_records = []
    seen = set()
    for r in records:
        key = (tuple(r["orders"]), r["label"], r["article"])
        if key not in seen:
            seen.add(key)
            unique_records.append(r)

    identifier_map = defaultdict(list)
    label_map = defaultdict(list)

    for record in unique_records:
        for identifier in record["identifiers"]:
            identifier = clean_identifier(identifier)
            if identifier:
                identifier_map[identifier].append(record)
        label = clean_identifier(record.get("label", ""))
        if label:
            label_map[label].append(record)

    return unique_records, identifier_map, label_map

def unique_record(records):
    if not records: return None
    unique = []
    seen = set()
    for r in records:
        key = (tuple(r.get("orders", [])), r.get("label", ""), r.get("article", ""))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    if len(unique) == 1: return unique[0]
    return None

def find_record_for_label(label_text, identifier_map, label_map):
    orders = extract_orders_from_text(label_text)
    for order in orders:
        key = clean_identifier(order)
        record = unique_record(identifier_map.get(key, []))
        if record: return record, "Полный номер"
        
        numeric = digits_only(order)
        record = unique_record(identifier_map.get(numeric, []))
        if record: return record, "Цифровой ключ"

    barcodes = extract_ii_barcodes(label_text)
    for barcode in barcodes:
        record = unique_record(identifier_map.get(clean_identifier(barcode), []))
        if record: return record, "Штрихкод ii"
        
        last4 = get_last4(barcode)
        if last4:
            record = unique_record(label_map.get(last4, []))
            if record: return record, "4 цифры штрихкода"

    codes = list(dict.fromkeys(re.findall(r"(?<!\d)(\d{4})(?!\d)", label_text)))
    for code in codes:
        record = unique_record(label_map.get(code, []))
        if record: return record, "4 цифры с этикетки"

    return None, "Не найдено"

# ============================================================
# ГЕНЕРАЦИЯ PDF
# ============================================================

def create_info_page(width, height, order, article, product, qty):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))
    margin = 15
    y = height - 25

    c.setFont(PDF_FONT, 12)
    c.drawString(margin, y, f"Заказ: {order}")
    c.line(margin, y - 5, width - margin, y - 5)
    
    y -= 25
    c.drawString(margin, y, f"Арт: {article or '-'}")
    y -= 20
    
    # Перенос названия товара
    c.setFont(PDF_FONT, 10)
    words = clean_spaces(product).split()
    line = ""
    for word in words:
        if len(line) + len(word) < 40:
            line += word + " "
        else:
            c.drawString(margin, y, line.strip())
            y -= 12
            line = word + " "
    if line:
        c.drawString(margin, y, line.strip())

    c.setFont(PDF_FONT, 20)
    c.drawString(margin, 20, f"КОЛ-ВО: {qty or '1'}")
    c.showPage()
    c.save()
    
    buffer.seek(0)
    return buffer.getvalue()

def process_files(labels_bytes, assembly_bytes):
    records, identifier_map, label_map = parse_assembly_pdf(assembly_bytes)
    
    label_reader = PdfReader(io.BytesIO(labels_bytes))
    writer = PdfWriter()

    matched = 0
    not_found = 0
    methods = defaultdict(int)
    diagnostics = []

    with pdfplumber.open(io.BytesIO(labels_bytes)) as labels_pdf:
        for page_index, page in enumerate(labels_pdf.pages):
            text = page.extract_text(x_tolerance=2, y_tolerance=3, layout=True) or ""
            text_no_underscores = re.sub(r'_\d+', '', text)
            
            record, method = find_record_for_label(text_no_underscores, identifier_map, label_map)

            original_page = label_reader.pages[page_index]
            writer.add_page(original_page)
            width, height = float(original_page.mediabox.width), float(original_page.mediabox.height)

            if record:
                matched += 1
                methods[method] += 1
                order = record["orders"][0] if record["orders"] else ""
                
                info_pdf = create_info_page(
                    width=width, height=height, order=order,
                    article=record.get("article", ""),
                    product=record.get("product", ""),
                    qty=record.get("qty", "1")
                )
                writer.add_page(PdfReader(io.BytesIO(info_pdf)).pages[0])
                
                diagnostics.append({
                    "page": page_index + 1, "status": "OK", "method": method,
                    "order": order, "article": record.get("article", ""),
                    "qty": record.get("qty", "1"), "label": record.get("label", "")
                })
            else:
                not_found += 1
                info_pdf = create_info_page(width, height, "НЕ НАЙДЕН", "", "Номер не распознан", "1")
                writer.add_page(PdfReader(io.BytesIO(info_pdf)).pages[0])
                
                found_orders = extract_orders_from_text(text)
                found_codes = re.findall(r"(?<!\d)(\d{4})(?!\d)", text)
                diagnostics.append({
                    "page": page_index + 1, "status": "NOT FOUND", "method": method,
                    "order": found_orders[0] if found_orders else "",
                    "label": found_codes[-1] if found_codes else ""
                })

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)

    return output.getvalue(), records, matched, not_found, methods, diagnostics

# ============================================================
# STREAMLIT ИНТЕРФЕЙС
# ============================================================

st.title("🖨️ Склейка: Этикетки + Лист подбора")
st.caption("Маппинг на основе физических блоков. Игнорирует изменения шапки таблицы Ozon.")

col1, col2 = st.columns(2)
with col1:
    labels_file = st.file_uploader("📦 Этикетки Ozon", type=["pdf"])
with col2:
    assembly_file = st.file_uploader("📋 Лист подбора Ozon", type=["pdf"])

if st.button("🚀 Сформировать PDF", type="primary", use_container_width=True):
    if not labels_file or not assembly_file:
        st.error("Загрузите оба PDF файла.")
        st.stop()

    with st.spinner("Разбираю графические блоки листа подбора..."):
        try:
            output, records, matched, not_found, methods, diagnostics = process_files(
                labels_file.getvalue(), assembly_file.getvalue()
            )
        except Exception as e:
            st.exception(e)
            st.stop()

    st.success(f"Готово. Найдено: {matched}. Не найдено: {not_found}.")

    c1, c2, c3 = st.columns(3)
    c1.metric("Записей листа подбора", len(records))
    c2.metric("Успешно привязано", matched)
    c3.metric("Ошибок", not_found)

    st.subheader("🔎 Результаты")
    for item in diagnostics[:50]:
        if item["status"] == "OK":
            st.write(f"✅ Стр. **{item['page']}** | Заказ: `{item['order']}` | Арт: `{item['article']}` | Кол-во: `{item['qty']}`")
        else:
            st.error(f"⚠️ Стр. **{item['page']}** — НЕ НАЙДЕНО | Заказ: `{item['order'] or '-'}` | 4 цифры: `{item['label'] or '-'}`")

    st.download_button(
        label="⬇️ Скачать готовый PDF",
        data=output,
        file_name="Ozon_Labels_Ready.pdf",
        mime="application/pdf",
        use_container_width=True
    )
