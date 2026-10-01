import streamlit as st
import pdfplumber
import pandas as pd
import re

st.set_page_config(page_title="Рентген PDF", layout="wide")
st.title("🛠 Отладочный скрипт (Рентген Листа подбора)")
st.write("Загрузите лист подбора, чтобы увидеть, как программа разбивает его на колонки.")

file = st.file_uploader("Загрузите Лист подбора (PDF)", type="pdf")

if file:
    with pdfplumber.open(file) as pdf:
        page = pdf.pages[0]
        
        # 1. Показываем сырую таблицу
        st.subheader("1. Структура таблицы (Ячейки)")
        table = page.extract_table({
            "vertical_strategy": "text",
            "horizontal_strategy": "text"
        })
        
        if table:
            # Превращаем в датафрейм для красивого отображения
            df = pd.DataFrame(table)
            st.dataframe(df, use_container_width=True)
            
            # 2. Показываем, что конкретно парсер пытается вытащить
            st.subheader("2. Что парсер видит в последних колонках")
            debug_data = []
            
            for i, row in enumerate(table):
                row_clean = [str(c).strip() if c else "" for c in row]
                if len(row_clean) < 4:
                    continue
                
                # Имитируем логику нашего парсера
                col_orders = ""
                for cell in row_clean:
                    if re.search(r'(\d{8,15}-\d{4}-\d+|[a-zA-Z0-9]{0,3}500\d{6,10})', cell):
                        col_orders = cell
                        break
                        
                if not col_orders:
                    continue
                    
                debug_data.append({
                    "Строка №": i,
                    "Найденные номера": col_orders.replace('\n', ' | '),
                    "Предполагаемый Артикул (row[-3])": row_clean[-3] if len(row_clean) >= 4 else "-",
                    "Предполагаемое Кол-во (row[-2])": row_clean[-2] if len(row_clean) >= 3 else "-",
                    "Предполагаемая Этикетка (row[-1])": row_clean[-1]
                })
                
            st.table(debug_data)
        else:
            st.error("pdfplumber вообще не видит здесь таблицу!")
            
        # 3. Сырой текст с отступами
        st.subheader("3. Сырой текст (С сохранением визуальных пробелов)")
        st.code(page.extract_text(layout=True), language="text")