import re
import pdfplumber
import fitz  # PyMuPDF для работы с PDF-этикетками

def parse_assembly_list(pdf_file):
    """
    Парсит лист сборки Ozon и возвращает словарь с данными о товарах.
    """
    data = {}
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            # 1. Находим горизонтальные разделители (длина > 30)
            lines = [line for line in page.lines if line['width'] > 30]
            lines.sort(key=lambda x: x['top'])
            
            # 2. Создаем границы строк (y-координаты)
            y_coords = [0] + [line['top'] for line in lines] + [page.height]
            
            for i in range(len(y_coords) - 1):
                top = y_coords[i]
                bottom = y_coords[i+1]
                
                # Защита от нулевой или отрицательной высоты строки
                if bottom - top < 15: 
                    continue
                    
                # 3. Вырезаем горизонтальную строку
                bbox = (0, top, page.width, bottom)
                try:
                    crop = page.within_bbox(bbox)
                except ValueError:
                    continue
                    
                # 4. Достаем слова с их координатами
                words = crop.extract_words()
                if not words:
                    continue
                    
                # 5. Настройка колонок (координаты X в пунктах)
                X_ORDER_MIN, X_ORDER_MAX = 20, 180   # Номер отправления
                X_NAME_MIN, X_NAME_MAX = 200, 420    # Товар
                X_ART_MIN, X_ART_MAX = 420, 490      # Артикул
                X_QTY_MIN, X_QTY_MAX = 490, 540      # Кол-во
                
                order_words, name_words, art_words, qty_words = [], [], [], []
                
                for w in words:
                    x = w['x0']
                    text = w['text']
                    
                    if X_ORDER_MIN <= x < X_ORDER_MAX:
                        order_words.append(text)
                    elif X_NAME_MIN <= x < X_NAME_MAX:
                        name_words.append(text)
                    elif X_ART_MIN <= x < X_ART_MAX:
                        art_words.append(text)
                    elif X_QTY_MIN <= x < X_QTY_MAX:
                        qty_words.append(text)
                        
                order_text = " ".join(order_words)
                name_text = " ".join(name_words).strip()
                art_text = " ".join(art_words).strip()
                qty_text = " ".join(qty_words).strip()
                
                # 6. Ищем номер заказа
                order_pattern = r'(\d{8,15}-\d{4}-\d+|[a-zA-Z]{0,4}\d{10,15})'
                order_match = re.search(order_pattern, order_text)
                
                if not order_match:
                    full_text = " ".join([w['text'] for w in words])
                    order_match = re.search(order_pattern, full_text)
                    
                if not order_match:
                    continue
                    
                full_num = order_match.group(1)
                order_norm = full_num.lower().replace('і', 'i').replace('І', 'i')
                
                # 7. Формируем короткий код
                if '-' not in order_norm:
                    short_code = order_norm[-4:]
                else:
                    short_code = order_norm.split('-')[0][-4:]
                    
                # 8. Очищаем данные из колонок
                name = name_text if name_text and len(name_text) > 1 else "Товар"
                article = art_text if art_text else "-"
                
                qty_match = re.search(r'\d+', qty_text)
                qty = qty_match.group(0) if qty_match else "1"
                
                item = {"name": name, "article": article, "qty": qty}
                
                # 9. Сохраняем в словарь
                num_key = re.sub(r'\D', '', order_norm)
                
                # ИСПРАВЛЕНИЕ: Используем списки, чтобы не перезаписать товары, если в заказе их несколько
                for key in [short_code, num_key] + ([num_key[-10:]] if len(num_key) >= 10 else []):
                    if key not in data:
                        data[key] = []
                    # Избегаем дублирования одного и того же товара по разным ключам в рамках списка
                    if item not in data[key]:
                        data[key].append(item)
                    
    return data

def process_labels(labels_pdf_path, list_pdf_path, output_pdf_path, font_path=None):
    """
    Наносит информацию о товаре на этикетки.
    """
    print("Парсинг листа сборки...")
    assembly_data = parse_assembly_list(list_pdf_path)
    
    print("Обработка этикеток...")
    doc = fitz.open(labels_pdf_path)
    
    for page in doc:
        # Ищем номер заказа на самой этикетке
        text = page.get_text()
        order_match = re.search(r'(\d{8,15}-\d{4}-\d+)', text)
        
        if not order_match:
            continue
            
        full_num = order_match.group(1)
        num_key = re.sub(r'\D', '', full_num)
        
        # Получаем список товаров для этого отправления
        items = assembly_data.get(num_key)
        
        if items:
            # Если товаров несколько, склеиваем их названия/артикулы в одну строку
            combined_name = " + ".join([f"{i['name'][:30]} ({i['qty']}шт)" for i in items])
            combined_art = " + ".join([i['article'] for i in items])
            
            info_text = f"Арт: {combined_art} | {combined_name}"
            
            # Размещаем текст внизу этикетки
            rect = page.rect
            text_y = rect.height - 20 
            text_x = 10
            
            # Если нужен русский язык, PyMuPDF требует загрузки шрифта (например, arial.ttf)
            # Если font_path не передан, кириллица может отображаться некорректно в стандартном шрифте
            if font_path:
                page.insert_font(fontname="ru_font", fontfile=font_path)
                page.insert_text((text_x, text_y), info_text[:100], fontsize=8, fontname="ru_font", color=(0,0,0))
            else:
                # Встроенный fallback для кириллицы (может работать не на всех системах)
                page.insert_text((text_x, text_y), info_text[:100], fontsize=8, fontname="helv", encoding=fitz.TEXT_ENCODING_CYRILLIC)

    doc.save(output_pdf_path)
    print(f"Готово! Сохранено в {output_pdf_path}")

# Пример запуска:
# process_labels("ozon_labels.pdf", "ozon_list.pdf", "result.pdf", font_path="arial.ttf")
