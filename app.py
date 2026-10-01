import streamlit as st
import requests
import io
import os
import time
from datetime import datetime
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

### ============================================================
### НАСТРОЙКИ СТРАНИЦЫ И API
### ============================================================
st.set_page_config(page_title="Ozon FBS — Умная склейка", page_icon="📦", layout="wide")

POSTINGS_URL = "https://api-seller.ozon.ru/v1/assembly/fbs/posting/list"
LABEL_URL = "https://api-seller.ozon.ru/v2/posting/fbs/package-label"

### ============================================================
### ЗАГРУЗКА ШРИФТА
### ============================================================
@st.cache_resource
def load_font():
    # Меняем имя файла на v3, чтобы принудительно сбросить сломанный кэш Streamlit Cloud
    font_path = "Roboto-v3.ttf"
    
    if not os.path.exists(font_path):
        # Используем проверенную стабильную ссылку из вашего первого скрипта
        url = "https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.1.66/fonts/Roboto/Roboto-Regular.ttf"
        r = requests.get(url)
        
        # Проверяем, что скачался именно файл шрифта (статус 200)
        if r.status_code == 200:
            with open(font_path, 'wb') as f:
                f.write(r.content)
        else:
            st.error("Не удалось скачать шрифт. Проверьте подключение к интернету.")
            st.stop()
            
    pdfmetrics.registerFont(TTFont('OzonFont', font_path))
    return 'OzonFont'

FONT_NAME = load_font()

### ============================================================
### ФУНКЦИИ API
### ============================================================
def get_headers(client_id, api_key):
    return {
        "Client-Id": str(client_id),
        "Api-Key": str(api_key),
        "Content-Type": "application/json"
    }

def get_fbs_postings(client_id, api_key, date_value):
    start_dt = datetime.combine(date_value, datetime.min.time()).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    end_dt = datetime.combine(date_value, datetime.max.time()).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    
    headers = get_headers(client_id, api_key)
    all_postings = []
    cursor = ""
    
    while True:
        payload = {
            "filter": {"cutoff_from": start_dt, "cutoff_to": end_dt},
            "limit": 1000,
            "sort_dir": "ASC"
        }
        if cursor:
            payload["cursor"] = cursor
            
        response = requests.post(POSTINGS_URL, headers=headers, json=payload, timeout=60)
        
        if response.status_code != 200:
            raise Exception(f"Ошибка API: {response.status_code} - {response.text}")
            
        data = response.json().get("result", {})
        postings = data.get("postings") or data.get("items") or []
        all_postings.extend(postings)
        
        next_cursor = data.get("cursor", "")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        
    return all_postings

def get_single_label(client_id, api_key, posting_number):
    headers = get_headers(client_id, api_key)
    payload = {"posting_number": [posting_number]}
    
    for attempt in range(3):
        try:
            response = requests.post(LABEL_URL, headers=headers, json=payload, timeout=30)
            if response.status_code == 200 and response.content.startswith(b"%PDF"):
                return response.content, None
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
        except Exception as e:
            error_msg = str(e)
        time.sleep(1)
        
    return None, error_msg

### ============================================================
### ГЕНЕРАЦИЯ ИНФО-ЭТИКЕТКИ (ЛИСТА ПОДБОРА)
### ============================================================
def create_info_label(width, height, posting_number, products):
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=(width, height))
    
    x_margin = 15
    y_current = height - 25

    # Заголовок - номер заказа
    c.setFont(FONT_NAME, 14)
    c.drawString(x_margin, y_current, f"FBS: {posting_number}")
    c.line(x_margin, y_current - 5, width - x_margin, y_current - 5)
    y_current -= 25
    
    # Подсчет общего количества товаров
    total_qty = sum([p.get('quantity', p.get('qty', 1)) for p in products])
    
    # Список товаров
    for prod in products:
        if y_current < 50: 
            c.setFont(FONT_NAME, 10)
            c.drawString(x_margin, y_current, "... (есть еще товары)")
            break
            
        article = prod.get('offer_id', prod.get('sku', ''))
        name = prod.get('name', 'Товар')
        qty = prod.get('quantity', prod.get('qty', 1))
        
        c.setFont(FONT_NAME, 10)
        c.drawString(x_margin, y_current, f"Арт: {article} (шт: {qty})")
        y_current -= 12
        
        # Перенос длинного названия
        words = name.split()
        line = ""
        for word in words:
            if len(line) + len(word) < 45:
                line += word + " "
            else:
                c.setFont(FONT_NAME, 9)
                c.drawString(x_margin, y_current, line.strip())
                y_current -= 12
                line = word + " "
        if line:
            c.setFont(FONT_NAME, 9)
            c.drawString(x_margin, y_current, line.strip())
            y_current -= 18
            
    # Крупно общее количество внизу
    c.setFont(FONT_NAME, 24)
    c.drawString(x_margin, 20, f"ВСЕГО КОЛ-ВО: {total_qty}")
    
    c.save()
    packet.seek(0)
    return PdfReader(packet).pages[0]

### ============================================================
### ИНТЕРФЕЙС И ОСНОВНАЯ ЛОГИКА
### ============================================================
st.title("📦 Ozon FBS — Умная склейка через API")
st.caption("100% точный маппинг. Этикетки сопоставляются с товарами напрямую через запросы к Ozon.")

st.sidebar.header("🔐 Настройки API")
client_id = st.sidebar.text_input("Client-Id", type="password")
api_key = st.sidebar.text_input("Api-Key", type="password")
selected_date = st.sidebar.date_input("Дата сборки (по cutoff)", value=datetime.now().date())

if not client_id or not api_key:
    st.info("👈 Введите Client-Id и Api-Key в боковом меню для начала работы.")
    st.stop()

if st.button("🚀 Получить и склеить этикетки", type="primary", use_container_width=True):
    
    with st.status("📡 Запрашиваем отправления...") as status:
        try:
            postings = get_fbs_postings(client_id, api_key, selected_date)
            if not postings:
                status.update(label="Нет отправлений на эту дату.", state="error")
                st.stop()
        except Exception as e:
            status.update(label="Ошибка получения отправлений", state="error")
            st.error(str(e))
            st.stop()
            
        status.update(label=f"Найдено {len(postings)} отправлений. Начинаем загрузку этикеток...")
        
        final_pdf = PdfWriter()
        progress_bar = st.progress(0)
        
        success_count = 0
        errors = []
        
        for idx, posting in enumerate(postings):
            posting_number = posting.get("posting_number")
            products = posting.get("products", [])
            
            # Если products пустой, пробуем взять данные из корня
            if not products and "offer_id" in posting:
                products = [posting]

            # 1. Скачиваем PDF конкретно для этого отправления
            pdf_bytes, error = get_single_label(client_id, api_key, posting_number)
            
            if pdf_bytes:
                label_reader = PdfReader(io.BytesIO(pdf_bytes))
                
                # Добавляем все страницы этикетки (обычно она одна)
                for page in label_reader.pages:
                    final_pdf.add_page(page)
                    
                    # Берем размеры оригинальной этикетки Ozon
                    w, h = float(page.mediabox.width), float(page.mediabox.height)
                    
                    # 2. Генерируем инфо-страницу такого же размера и с точными данными API
                    info_page = create_info_label(w, h, posting_number, products)
                    final_pdf.add_page(info_page)
                    
                success_count += 1
            else:
                errors.append(f"{posting_number}: {error}")
                
            progress_bar.progress((idx + 1) / len(postings))
            
        status.update(label="Формирование итогового файла завершено!", state="complete")

    # ========================================================
    # РЕЗУЛЬТАТЫ
    # ========================================================
    st.divider()
    col1, col2 = st.columns(2)
    col1.metric("Успешно сформировано", f"{success_count} / {len(postings)}")
    col2.metric("Ошибок получения от Ozon", len(errors))
    
    if errors:
        st.error("⚠️ Следующие отправления не получили этикетку от Ozon (возможно, они отменены или статус не позволяет):")
        with st.expander("Показать список ошибок"):
            for err in errors:
                st.write(err)

    if success_count > 0:
        result_stream = io.BytesIO()
        final_pdf.write(result_stream)
        
        st.success("✅ Файл полностью готов к печати! Каждая этикетка идет строго перед своим листом подбора.")
        st.download_button(
            label="📥 Скачать готовый PDF",
            data=result_stream.getvalue(),
            file_name=f"Ozon_Labels_{selected_date.strftime('%Y-%m-%d')}.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True
        )
