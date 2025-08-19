from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash, jsonify
from sqlalchemy import create_engine, inspect, text
import os
import io
from datetime import datetime
import tempfile
import shutil
import zipfile
import xml.etree.ElementTree as ET
import uuid
from werkzeug.utils import secure_filename
import logging

# Настройка логгирования
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Создаем экземпляр Flask приложения
orex = Flask(__name__)
orex.secret_key = os.urandom(24).hex()  # Автогенерация ключа
orex.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Глобальный движок для упрощения
engine = None

# Константы
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRINT_TEMPLATES_DIR = os.path.join(BASE_DIR, 'print-templates')
ALLOWED_EXTENSIONS = {'odt'}

# Создаем папку для шаблонов, если ее нет
if not os.path.exists(PRINT_TEMPLATES_DIR):
    os.makedirs(PRINT_TEMPLATES_DIR)
    logger.info(f"Created templates directory: {PRINT_TEMPLATES_DIR}")

# Функция для проверки расширения файла
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Функция для получения метаданных таблицы
def get_table_metadata(table_name):
    """Получаем детальную информацию о колонках таблицы"""
    try:
        inspector = inspect(engine)
        columns = []
        
        for col in inspector.get_columns(table_name):
            # Определяем тип данных
            col_type = str(col['type'])
            if 'INT' in col_type:
                data_type = 'INTEGER'
            elif 'VARCHAR' in col_type or 'TEXT' in col_type:
                data_type = 'TEXT'
            elif 'DATE' in col_type:
                data_type = 'DATE'
            elif 'DATETIME' in col_type or 'TIMESTAMP' in col_type:
                data_type = 'DATETIME'
            elif 'BOOLEAN' in col_type:
                data_type = 'BOOLEAN'
            else:
                data_type = col_type
            
            # Форматируем значение по умолчанию
            default_value = col.get('default')
            if isinstance(default_value, str) and default_value.startswith("'") and default_value.endswith("'"):
                default_value = default_value[1:-1]
            
            column_info = {
                'name': col['name'],
                'type': data_type,
                'nullable': col['nullable'],
                'default': default_value,
                'autoincrement': col.get('autoincrement', False),
                'primary_key': col.get('primary_key', False)
            }
            columns.append(column_info)
        
        return columns
    
    except Exception as e:
        logger.error(f"Error getting metadata for {table_name}: {str(e)}")
        raise

# Функция для получения списка шаблонов
def get_template_list():
    """Возвращает список доступных шаблонов"""
    try:
        return [f for f in os.listdir(PRINT_TEMPLATES_DIR) if f.endswith('.odt')]
    except Exception as e:
        logger.error(f"Error listing templates: {str(e)}")
        return []

# Функция для обработки шаблона (ИСПРАВЛЕННАЯ ВЕРСИЯ)
def process_odt_template(template_path, data):
    """Обрабатывает ODT шаблон, подставляя данные"""
    # Создаем временную папку для работы
    temp_dir = tempfile.mkdtemp()
    logger.debug(f"Created temp directory: {temp_dir}")
    
    try:
        # Распаковываем ODT файл (это ZIP архив)
        with zipfile.ZipFile(template_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        # Основной контент находится в content.xml
        content_path = os.path.join(temp_dir, 'content.xml')
        
        # Читаем и парсим XML
        tree = ET.parse(content_path)
        root = tree.getroot()
        
        # Создаем словарь замен
        replacements = {f'${key}': str(value) for key, value in data.items()}
        
        # Функция для рекурсивной замены в элементах
        def replace_in_element(elem, reps):
            # Обрабатываем текст элемента
            if elem.text:
                for ph, value in reps.items():
                    if ph in elem.text:
                        logger.debug(f"Replacing {ph} with {value}")
                        elem.text = elem.text.replace(ph, value)
            
            # Обрабатываем tail элемента
            if elem.tail:
                for ph, value in reps.items():
                    if ph in elem.tail:
                        elem.tail = elem.tail.replace(ph, value)
            
            # Рекурсивно обрабатываем дочерние элементы
            for child in elem:
                replace_in_element(child, reps)
        
        # Выполняем замену во всем дереве XML
        replace_in_element(root, replacements)
        
        # Сохраняем изменения
        tree.write(content_path, encoding='utf-8', xml_declaration=True)
        
        # Собираем обратно в ODT
        processed_path = os.path.join(temp_dir, 'processed.odt')
        with zipfile.ZipFile(processed_path, 'w') as zip_ref:
            for folder, _, files in os.walk(temp_dir):
                for file in files:
                    if file != 'processed.odt':  # Не включаем сам файл архива
                        file_path = os.path.join(folder, file)
                        arc_path = os.path.relpath(file_path, temp_dir)
                        zip_ref.write(file_path, arc_path)
        
        return processed_path, temp_dir
    
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.error(f"Template processing error: {str(e)}")
        raise RuntimeError(f"Ошибка обработки шаблона: {str(e)}")

@orex.route('/orex-ws/login', methods=['GET', 'POST'])
def login():
    global engine
    
    if request.method == 'POST':
        try:
            # Берём данные из формы
            host = request.form['host'] or 'localhost'
            user = request.form['username']
            password = request.form['password']
            database = request.form['database']
            
            # Пробуем подключиться
            engine = create_engine(
                f"mysql+pymysql://{user}:{password}@{host}/{database}"
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))  # Простейший запрос для проверки
            
            # Сохраняем в сессию только для флага
            session['logged_in'] = True
            return redirect(url_for('base'))
        
        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            return render_template('login.html', error=str(e))
    
    return render_template('login.html')

@orex.route('/orex-ws/logout')
def logout():
    global engine
    session.pop('logged_in', None)
    engine = None
    return redirect(url_for('login'))

@orex.route('/orex-ws')
def base():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    try:
        inspector = inspect(engine)
        return render_template('base.html', tables=inspector.get_table_names())
    
    except Exception as e:
        logger.error(f"Base error: {str(e)}")
        return render_template('error.html', error=str(e))

@orex.route('/orex-ws/table', methods=['GET', 'POST'])
def show_table():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    table_name = request.args.get('name')
    if not table_name:
        return redirect(url_for('base'))
    
    try:
        inspector = inspect(engine)
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        primary_keys = inspector.get_pk_constraint(table_name)['constrained_columns']
        primary_key = primary_keys[0] if primary_keys else columns[0]
        
        with engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(text(f"SELECT * FROM `{table_name}`")).mappings()]
        
        # Получаем список шаблонов
        templates = get_template_list()
        
        if request.method == 'POST':
            # Обработка загрузки шаблона
            if 'template_file' in request.files:
                file = request.files['template_file']
                if file.filename == '':
                    flash('Не выбран файл для загрузки', 'danger')
                elif not allowed_file(file.filename):
                    flash('Разрешены только файлы .odt', 'danger')
                else:
                    # Генерируем уникальное имя файла
                    filename = secure_filename(file.filename)
                    unique_name = f"{uuid.uuid4().hex[:8]}_{filename}"
                    save_path = os.path.join(PRINT_TEMPLATES_DIR, unique_name)
                    
                    try:
                        file.save(save_path)
                        flash(f'Шаблон "{filename}" успешно загружен!', 'success')
                        logger.info(f"Template uploaded: {save_path}")
                        # Возвращаемся на ту же страницу
                        return redirect(url_for('show_table', name=table_name))
                    except Exception as e:
                        logger.error(f"Template save error: {str(e)}")
                        flash(f'Ошибка при сохранении файла: {str(e)}', 'danger')
            
            # Генерация ответа на письмо
            elif 'row_id' in request.form:
                row_id = request.form['row_id']
                template_name = request.form.get('template')
                selected_row = next((row for row in rows if str(row[primary_key]) == row_id), None)
                
                if selected_row and template_name:
                    # Полный путь к шаблону
                    template_path = os.path.join(PRINT_TEMPLATES_DIR, template_name)
                    
                    if not os.path.exists(template_path):
                        flash(f'Шаблон {template_name} не найден', 'danger')
                    else:
                        # Обрабатываем шаблон
                        try:
                            logger.debug(f"Processing template: {template_path}")
                            logger.debug(f"Using data: {selected_row}")
                            
                            processed_path, temp_dir = process_odt_template(template_path, selected_row)
                            
                            # Отправляем результат
                            response = send_file(
                                processed_path,
                                as_attachment=True,
                                download_name=f'response_{row_id}.odt',
                                mimetype='application/vnd.oasis.opendocument.text'
                            )
                            
                            # Удаляем временные файлы после отправки
                            response.call_on_close(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
                            return response
                        except Exception as e:
                            logger.error(f"Template processing error: {str(e)}")
                            flash(f'Ошибка обработки шаблона: {str(e)}', 'danger')
        
        return render_template('table.html',
                              table_name=table_name,
                              columns=columns,
                              rows=rows,
                              primary_key=primary_key,
                              templates=templates)
    
    except Exception as e:
        logger.error(f"Show table error: {str(e)}")
        return render_template('error.html', error=str(e))

# Обновленный маршрут для формы добавления записи
@orex.route('/orex-ws/vvod', methods=['GET'])
def vvod():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    table_name = request.args.get('table')
    if not table_name:
        return redirect(url_for('base'))
    
    try:
        # Получаем детальные метаданные
        columns_meta = get_table_metadata(table_name)
        
        # Фильтруем только те колонки, которые нужно показать
        visible_columns = [col for col in columns_meta if not col['autoincrement']]
        
        return render_template('vvod.html', 
                              table_name=table_name, 
                              columns=visible_columns,
                              all_columns=columns_meta)
    
    except Exception as e:
        logger.error(f"Vvod error: {str(e)}")
        return render_template('error.html', error=str(e))

# Маршрут для сохранения записи
@orex.route('/orex-ws/save_record', methods=['POST'])
def save_record():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    table_name = request.form.get('table_name')
    if not table_name:
        return redirect(url_for('base'))
    
    try:
        # Получаем полную метаинформацию о таблице
        columns_meta = get_table_metadata(table_name)
        data = {}
        
        for col in columns_meta:
            col_name = col['name']
            form_value = request.form.get(col_name)
            
            # Для автоинкрементных полей ничего не делаем
            if col['autoincrement']:
                continue
                
            # Обработка разных случаев
            if form_value is None or form_value == '':
                if col['nullable']:
                    data[col_name] = None
                elif col['default'] is not None:
                    continue
                else:
                    flash(f'Поле "{col_name}" обязательно для заполнения', 'danger')
                    return redirect(url_for('vvod', table=table_name))
            else:
                if col['type'] == 'BOOLEAN' or 'галочка' in col_name.lower():
                    data[col_name] = 1 if form_value == '1' else 0
                elif col['type'] == 'DATE' and form_value:
                    data[col_name] = datetime.strptime(form_value, '%Y-%m-%d').date()
                elif col['type'] == 'DATETIME' and form_value:
                    data[col_name] = datetime.strptime(form_value, '%Y-%m-%dT%H:%M')
                else:
                    data[col_name] = form_value
        
        # Строим запрос на вставку
        columns_str = ', '.join([f'`{col}`' for col in data.keys()])
        values_str = ', '.join([f':{col}' for col in data.keys()])
        insert_query = text(f"INSERT INTO `{table_name}` ({columns_str}) VALUES ({values_str})")
        
        with engine.begin() as conn:
            conn.execute(insert_query, data)
        
        flash('Запись успешно добавлена!', 'success')
        return redirect(f'/orex-ws/table?name={table_name}')
    
    except Exception as e:
        logger.error(f"Save record error: {str(e)}")
        flash(f'Ошибка при сохранении: {str(e)}', 'danger')
        return redirect(url_for('vvod', table=table_name))

# Маршрут для удаления шаблонов
@orex.route('/orex-ws/delete_template', methods=['POST'])
def delete_template():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Требуется авторизация'}), 401
    
    try:
        data = request.get_json()
        template_name = data.get('template_name')
        
        if not template_name:
            return jsonify({'success': False, 'message': 'Не указано имя шаблона'}), 400
        
        template_path = os.path.join(PRINT_TEMPLATES_DIR, template_name)
        
        if not os.path.exists(template_path):
            return jsonify({'success': False, 'message': 'Шаблон не найден'}), 404
        
        os.remove(template_path)
        logger.info(f"Template deleted: {template_path}")
        return jsonify({
            'success': True,
            'message': f'Шаблон "{template_name}" успешно удален'
        })
    
    except Exception as e:
        logger.error(f"Delete template error: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Ошибка при удалении шаблона: {str(e)}'
        }), 500

# Новый маршрут для удаления записи
@orex.route('/orex-ws/delete_record', methods=['POST'])
def delete_record():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    table_name = request.form['table_name']
    row_id = request.form['row_id']
    primary_key = request.form['primary_key']
    
    try:
        with engine.begin() as conn:
            # Получаем текущий максимальный ID
            result = conn.execute(text(f"SELECT MAX(`{primary_key}`) as max_id FROM `{table_name}`"))
            max_id_row = result.fetchone()
            current_max = max_id_row['max_id'] if max_id_row else None
            
            # Проверяем, что удаляемая запись - последняя
            if current_max is None or int(row_id) != current_max:
                flash('Можно удалять только последнюю запись!', 'danger')
                return redirect(url_for('show_table', name=table_name))
            
            # Удаляем запись
            conn.execute(text(f"DELETE FROM `{table_name}` WHERE `{primary_key}` = :id"), {'id': row_id})
            
            # Устанавливаем автоинкремент на значение удаленной записи
            new_auto_increment = int(row_id)
            conn.execute(text(f"ALTER TABLE `{table_name}` AUTO_INCREMENT = {new_auto_increment}"))
        
        flash('Запись успешно удалена', 'success')
        return redirect(f'/orex-ws/table?name={table_name}')
    
    except Exception as e:
        logger.error(f"Delete record error: {str(e)}")
        flash(f'Ошибка при удалении: {str(e)}', 'danger')
        return redirect(url_for('show_table', name=table_name))

if __name__ == "__main__":
    orex.run(host='0.0.0.0', port=5000, debug=True)