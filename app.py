import streamlit as st
import re
import os
import requests
from io import BytesIO
from datetime import datetime, timedelta

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ============================================================
# НАСТРОЙКИ СТРАНИЦЫ
# ============================================================
st.set_page_config(
    page_title="Ozon FBS — Этикетки (Авто-маппинг API)",
    page_icon="🖨️",
    layout="wide"
)

st.title("🖨️ Ozon FBS — Умные этикетки (API)")
st.write("Скрипт автоматически получает лист подбора по API и добавляет артикулы к этикеткам.")

# ============================================================
# ШРИФТ
# ============================================================
@st.cache_resource
def load_font():
    font_path = "Roboto_Full_Final.ttf"
    if not os.path.exists(font_path):
        url = "https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.1.66/fonts/Roboto/Roboto-Regular.ttf"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        with open(font_path, "wb") as f:
            f.write(response.content)
    try:
        pdfmetrics.registerFont(TTFont("OzonFont", font_path))
    except Exception:
        pass
    return "OzonFont"

font_name = load_font()

# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def normalize_shipment(value):
    if not value: return ""
    value = str(value)
    value = re.sub(r"\s+", "", value)
    value = value.replace("І", "I").replace("і", "i")
    return value.lower()

def extract_shipment_from_label(page):
    text = page.extract_text()
    if not text: return None
    match = re.search(r"\d{8,15}-\d{4}-\d+", text)
    if match: return normalize_shipment(match.group(0))
    clean = re.sub(r"\s+", "", text)
    match = re.search(r"ii\d{8,20}", clean, flags=re.IGNORECASE)
    if match: return normalize_shipment(match.group(0))
    return None

# ============================================================
# API OZON (ЗАМЕНА ПАРСИНГУ PDF)
# ============================================================
def get_assembly_data_from_api(client_id: str, api_key: str, days_back: int = 5, days_forward: int = 5):
    url = "https://api-seller.ozon.ru/v1/assembly/fbs/posting/list"
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }
    
    now = datetime.utcnow()
    cutoff_from = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cutoff_to = (now + timedelta(days=days_forward)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    data_mapping = {}
    cursor = ""
    
    while True:
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
            
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            raise Exception(f"Ошибка API Ozon: {response.text}")
            
        result = response.json()
        postings = result.get("postings", [])
        
        for posting in postings:
            posting_number = posting.get("posting_number")
            products = posting.get("products", [])
            
            names = []
            articles = []
            total_qty = 0
            
            for prod in products:
                names.append(prod.get("product_name", ""))
                articles.append(str(prod.get("offer_id", "")))
                total_qty += prod.get("quantity", 0)
            
            key = posting_number.lower()
            data_mapping[key] = {
                "shipment": posting_number,
                "article": " + ".join(articles),
                "qty": str(total_qty),
                "name": " + ".join(names),
                "label": "-"
            }
            
        cursor = result.get("cursor")
        if not cursor:
            break
            
    return data_mapping

# ============================================================
# ГЕНЕРАЦИЯ СТРАНИЦЫ С ИНФОРМАЦИЕЙ
# ============================================================
def create_info_label(width, height, order_number, product_info):
    packet = BytesIO()
    c = canvas.Canvas(packet, pagesize=(width, height))
    margin = 10

    # Заказ
    c.setFont(font_name, 9)
    c.drawString(margin, height - 16, f"Заказ: {order_number}")
    c.line(margin, height - 20, width - margin, height - 20)

    # Этикетка (заглушка для API)
    c.setFont(font_name, 9)
    c.drawString(margin, height - 32, f"Этикетка: {product_info.get('label', '-')}")

    # Артикул
    c.setFont(font_name, 11)
    c.drawString(margin, height - 47, f"Арт: {product_info.get('article', '-')}")

    # Название товара
    name = product_info.get("name", "Товар не найден")
    top = height - 63
    bottom = 48
    font_size = 9
    line_height = 11

    def split_text(text, chars):
        words = text.split()
        result = []
        current = ""
        for word in words:
            test = current + word + " "
            if len(test) <= chars: current = test
            else:
                if current: result.append(current.strip())
                current = word + " "
        if current: result.append(current.strip())
        return result

    lines = split_text(name, 30)
    while (len(lines) * line_height > top - bottom and font_size > 6):
        font_size -= 0.5
        line_height -= 0.5
        lines = split_text(name, max(20, int(30 * 9 / font_size)))

    c.setFont(font_name, font_size)
    y = top
    for line in lines:
        if y <= bottom: break
        c.drawString(margin, y, line)
        y -= line_height

    # Количество
    c.setFont(font_name, 22)
    c.drawString(margin, 14, f"КОЛ-ВО: {product_info.get('qty', '?')}")

    c.save()
    packet.seek(0)
    return PdfReader(packet).pages[0]

# ============================================================
# UI: ПОЛЯ ВВОДА И КНОПКИ
# ============================================================
st.sidebar.header("🔑 Настройки API Ozon")
client_id = st.sidebar.text_input("Client-Id")
api_key = st.sidebar.text_input("Api-Key", type="password")

col1, col2 = st.columns(2)
with col1:
    labels_file = st.file_uploader("1️⃣ Этикетки Ozon (PDF)", type=["pdf"])
with col2:
    st.info("💡 Лист подбора загружать не нужно. Скрипт сам заберет данные из вашего кабинета через API.")

# ============================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================
if labels_file and client_id and api_key:
    if st.button("🚀 Склеить файлы", type="primary", use_container_width=True):
        
        with st.status("Получаем данные от Ozon...") as status:
            try:
                assembly_data = get_assembly_data_from_api(client_id, api_key)
                if not assembly_data:
                    status.update(label="Отправления не найдены по API", state="error")
                    st.stop()
                status.update(label=f"Успешно получено {len(assembly_data)} отправлений из API. Разбираем этикетки...")
            except Exception as e:
                status.update(label=f"Ошибка API: {str(e)}", state="error")
                st.stop()

            labels_bytes = labels_file.getvalue()
            reader = PdfReader(BytesIO(labels_bytes))
            writer = PdfWriter()

            found, not_found, unknown = 0, 0, 0

            for page in reader.pages:
                writer.add_page(page)
                shipment = extract_shipment_from_label(page)
                width = float(page.mediabox.width)
                height = float(page.mediabox.height)

                if shipment:
                    info = assembly_data.get(shipment.lower())
                    if info:
                        found += 1
                        display_number = info["shipment"].upper()
                    else:
                        not_found += 1
                        display_number = shipment.upper()
                        info = {"label": "-", "article": "-", "qty": "?", "name": "ОТПРАВЛЕНИЕ НЕ НАЙДЕНО В API"}
                else:
                    unknown += 1
                    display_number = "???"
                    info = {"label": "-", "article": "-", "qty": "-", "name": "НОМЕР НЕ РАСПОЗНАН НА ЭТИКЕТКЕ"}

                writer.add_page(create_info_label(width, height, display_number, info))

            status.update(label=f"Готово! Найдено: {found}, Не найдено: {not_found}, Не распознано: {unknown}", state="complete")

        output = BytesIO()
        writer.write(output)
        output.seek(0)

        st.download_button(
            "📥 Скачать готовые этикетки",
            output,
            "Ready_Labels.pdf",
            "application/pdf",
            use_container_width=True
        )

elif labels_file:
    st.warning("Введите Client-Id и Api-Key в боковой панели слева.")
