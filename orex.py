from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash, jsonify
from sqlalchemy import create_engine, inspect, text
import os
import io
from datetime import datetime, date
import tempfile
import shutil
import zipfile
import xml.etree.ElementTree as ET
import uuid
from werkzeug.utils import secure_filename
import logging
import json

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

# Security configuration
SECURITY_WHITELIST = 'security_whitelist.txt'
SECURITY_BLACKLIST = 'security_blacklist.txt'
LOGIN_LOG = 'login-log.txt'
ALLOWED_BROWSERS = ['Chrome', 'Firefox', 'Safari', 'Edge', 'OPR']  # Разрешенные браузеры

# Создаем папку для шаблонов, если ее нет
if not os.path.exists(PRINT_TEMPLATES_DIR):
    os.makedirs(PRINT_TEMPLATES_DIR)
    logger.info(f"Created templates directory: {PRINT_TEMPLATES_DIR}")

# Функции безопасности
def check_browser_allowed(user_agent):
    """Проверяет, разрешен ли браузер"""
    return any(browser in user_agent for browser in ALLOWED_BROWSERS)

def read_security_list(filename):
    """Читает security-лист"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip() and not line.startswith('#')]
    except FileNotFoundError:
        # Если файла нет, создаем пустой
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("# Security list\n")
        return []

def is_ip_banned(ip):
    """Проверяет, забанен ли IP"""
    blacklist = read_security_list(SECURITY_BLACKLIST)
    return any(ip in entry for entry in blacklist)

def add_to_blacklist(ip, reason="Multiple failed attempts"):
    """Добавляет в черный список"""
    with open(SECURITY_BLACKLIST, 'a', encoding='utf-8') as f:
        f.write(f"{ip}|{reason}|{datetime.now()}\n")

def add_to_whitelist(ip, fingerprint):
    """Добавляет в белый список"""
    with open(SECURITY_WHITELIST, 'a', encoding='utf-8') as f:
        f.write(f"{ip}|{fingerprint}|{datetime.now()}\n")

def check_whitelist(ip, fingerprint):
    """Проверяет, есть ли IP и отпечаток в белом списке"""
    whitelist = read_security_list(SECURITY_WHITELIST)
    return any(ip in entry and fingerprint in entry for entry in whitelist)

def log_login_attempt(ip, fingerprint, success, message=""):
    """Логирует попытку входа"""
    short_fingerprint = fingerprint[:10] if fingerprint else "none"
    status = "Success" if success else f"Failure: {message}"
    
    with open(LOGIN_LOG, 'a', encoding='utf-8') as f:
        f.write(f"[{datetime.now()}] {ip} {short_fingerprint} {status}\n")

# Глобальный словарь для отслеживания попыток входа
login_attempts = {}

@orex.before_request
def security_check():
    """Проверка безопасности для всех запросов"""
    # Пропускаем статические файлы и страницу логина
    if request.endpoint in ['login', 'static']:
        return
    
    # Проверяем авторизацию
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    # Проверяем безопасность
    ip = request.remote_addr
    user_agent = request.user_agent.string
    fingerprint = session.get('fingerprint', '')
    
    # Проверяем бан по IP
    if is_ip_banned(ip):
        return f"Доступ запрещен: IP заблокирован", 403
    
    # Проверяем соответствие IP и отпечатка в белом списке
    if not check_whitelist(ip, fingerprint):
        session.clear()
        return redirect(url_for('login'))

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

# Функция для обработки шаблона
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
    
    ip = request.remote_addr
    user_agent = request.user_agent.string
    
    # Проверка безопасности для GET запроса
    if request.method == 'GET':
        # Проверяем бан по IP
        if is_ip_banned(ip):
            return f"Доступ запрещен: IP заблокирован", 403
        
        # Проверяем браузер
        if not check_browser_allowed(user_agent):
            return "Доступ запрещен: Неподдерживаемый браузер", 403
        
        return render_template('login.html')
    
    # Обработка POST запроса
    fingerprint = request.form.get('fingerprint', '')
    
    # Проверяем бан по IP
    if is_ip_banned(ip):
        log_login_attempt(ip, fingerprint, False, "Banned IP")
        return "Доступ запрещен: IP заблокирован", 403
    
    # Проверяем браузер
    if not check_browser_allowed(user_agent):
        log_login_attempt(ip, fingerprint, False, "Invalid browser")
        return "Доступ запрещен: Неподдерживаемый браузер", 403
    
    # Проверяем попытки входа
    attempt_key = f"{ip}"
    if attempt_key in login_attempts and login_attempts[attempt_key] >= 3:
        add_to_blacklist(ip, "Too many failed attempts")
        log_login_attempt(ip, fingerprint, False, "Too many attempts")
        return "Слишком много неудачных попыток. Ваш IP заблокирован.", 403
    
    # Проверяем белый список (если IP уже есть в нем)
    whitelist = read_security_list(SECURITY_WHITELIST)
    ip_in_whitelist = any(ip in entry for entry in whitelist)
    
    if ip_in_whitelist and not check_whitelist(ip, fingerprint):
        log_login_attempt(ip, fingerprint, False, "Fingerprint mismatch")
        return "Доступ запрещен: Несоответствие отпечатка устройства", 403
    
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
        
        # Сбрасываем счетчик попыток
        if attempt_key in login_attempts:
            del login_attempts[attempt_key]
        
        # Добавляем в белый список, если IP еще не там
        if not ip_in_whitelist:
            add_to_whitelist(ip, fingerprint)
        
        # Сохраняем в сессию
        session['logged_in'] = True
        session['fingerprint'] = fingerprint
        session['ip'] = ip
        
        log_login_attempt(ip, fingerprint, True)
        return redirect(url_for('base'))
    
    except Exception as e:
        # Увеличиваем счетчик попыток
        login_attempts[attempt_key] = login_attempts.get(attempt_key, 0) + 1
        logger.error(f"Login error: {str(e)}")
        log_login_attempt(ip, fingerprint, False, str(e))
        return render_template('login.html', error=str(e))

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
    
    # Дополнительная проверка безопасности
    ip = request.remote_addr
    fingerprint = session.get('fingerprint', '')
    
    if ip != session.get('ip'):
        session.clear()
        return redirect(url_for('login'))

    if not check_whitelist(ip, fingerprint):
        session.clear()
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
    
    # Дополнительная проверка безопасности
    ip = request.remote_addr
    fingerprint = session.get('fingerprint', '')
    
    if ip != session.get('ip'):
        session.clear()
        return redirect(url_for('login'))

    if not check_whitelist(ip, fingerprint):
        session.clear()
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

@orex.route('/orex-ws/vvod', methods=['GET'])
def vvod():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    # Дополнительная проверка безопасности
    ip = request.remote_addr
    fingerprint = session.get('fingerprint', '')
    
    if ip != session.get('ip'):
        session.clear()
        return redirect(url_for('login'))

    if not check_whitelist(ip, fingerprint):
        session.clear()
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

@orex.route('/orex-ws/save_record', methods=['POST'])
def save_record():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    # Дополнительная проверка безопасности
    ip = request.remote_addr
    fingerprint = session.get('fingerprint', '')
    
    if ip != session.get('ip'):
        session.clear()
        return redirect(url_for('login'))

    if not check_whitelist(ip, fingerprint):
        session.clear()
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

@orex.route('/orex-ws/edit', methods=['GET'])
def edit_record():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    # Дополнительная проверка безопасности
    ip = request.remote_addr
    fingerprint = session.get('fingerprint', '')
    
    if ip != session.get('ip'):
        session.clear()
        return redirect(url_for('login'))

    if not check_whitelist(ip, fingerprint):
        session.clear()
        return redirect(url_for('login'))
    
    table_name = request.args.get('table_name')
    row_id = request.args.get('row_id')
    
    if not table_name or not row_id:
        return redirect(url_for('base'))
    
    try:
        # Получаем метаданные таблицы
        columns_meta = get_table_metadata(table_name)
        visible_columns = [col for col in columns_meta if not col['autoincrement']]
        
        # Определяем первичный ключ
        inspector = inspect(engine)
        primary_keys = inspector.get_pk_constraint(table_name)['constrained_columns']
        primary_key = primary_keys[0] if primary_keys else None
        
        # Получаем запись для редактирования
        with engine.connect() as conn:
            query = text(f"SELECT * FROM `{table_name}` WHERE `{primary_key}` = :id")
            result = conn.execute(query, {'id': row_id})
            record = result.mappings().first()
            
        if not record:
            flash('Запись не найдена', 'danger')
            return redirect(url_for('show_table', name=table_name))
        
        # Форматируем даты для HTML-полей
        formatted_record = dict(record)
        for key, value in formatted_record.items():
            if isinstance(value, datetime):
                formatted_record[key] = value.strftime('%Y-%m-%dT%H:%M')
            elif isinstance(value, date):
                formatted_record[key] = value.strftime('%Y-%m-%d')
        
        return render_template('edit.html',
                              table_name=table_name,
                              columns=visible_columns,
                              record=formatted_record,
                              primary_key=primary_key)
    
    except Exception as e:
        logger.error(f"Edit record error: {str(e)}")
        flash(f'Ошибка при открытии записи: {str(e)}', 'danger')
        return redirect(url_for('show_table', name=table_name))

@orex.route('/orex-ws/update_record', methods=['POST'])
def update_record():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    # Дополнительная проверка безопасности
    ip = request.remote_addr
    fingerprint = session.get('fingerprint', '')
    
    if ip != session.get('ip'):
        session.clear()
        return redirect(url_for('login'))

    if not check_whitelist(ip, fingerprint):
        session.clear()
        return redirect(url_for('login'))
    
    table_name = request.form.get('table_name')
    primary_key_value = request.form.get('primary_key_value')
    
    if not table_name or not primary_key_value:
        return redirect(url_for('base'))
    
    try:
        # Получаем метаданные таблицы
        columns_meta = get_table_metadata(table_name)
        inspector = inspect(engine)
        primary_keys = inspector.get_pk_constraint(table_name)['constrained_columns']
        primary_key = primary_keys[0] if primary_keys else None
        
        data = {}
        for col in columns_meta:
            col_name = col['name']
            
            # Пропускаем автоинкрементные поля и первичный ключ
            if col['autoincrement'] or col_name == primary_key:
                continue
                
            form_value = request.form.get(col_name)
            
            # Обработка значений
            if form_value is None or form_value == '':
                if col['nullable']:
                    data[col_name] = None
                elif col['default'] is not None:
                    continue
                else:
                    flash(f'Поле "{col_name}" обязательно для заполнения', 'danger')
                    return redirect(url_for('edit_record', table_name=table_name, row_id=primary_key_value))
            else:
                if col['type'] == 'BOOLEAN' or 'галочка' in col_name.lower():
                    data[col_name] = 1 if form_value == '1' else 0
                elif col['type'] == 'DATE' and form_value:
                    data[col_name] = datetime.strptime(form_value, '%Y-%m-%d').date()
                elif col['type'] == 'DATETIME' and form_value:
                    data[col_name] = datetime.strptime(form_value, '%Y-%m-%dT%H:%M')
                else:
                    data[col_name] = form_value
        
        # Строим UPDATE запрос
        set_clause = ', '.join([f'`{col}` = :{col}' for col in data.keys()])
        update_query = text(f"UPDATE `{table_name}` SET {set_clause} WHERE `{primary_key}` = :pk_value")
        
        # Добавляем значение первичного ключа
        data['pk_value'] = primary_key_value
        
        with engine.begin() as conn:
            conn.execute(update_query, data)
        
        flash('Запись успешно обновлена!', 'success')
        return redirect(f'/orex-ws/table?name={table_name}')
    
    except Exception as e:
        logger.error(f"Update record error: {str(e)}")
        flash(f'Ошибка при обновлении: {str(e)}', 'danger')
        return redirect(url_for('edit_record', table_name=table_name, row_id=primary_key_value))

@orex.route('/orex-ws/delete_template', methods=['POST'])
def delete_template():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Требуется авторизация'}), 401
    
    # Дополнительная проверка безопасности
    ip = request.remote_addr
    fingerprint = session.get('fingerprint', '')
    
    if ip != session.get('ip'):
        session.clear()
        return jsonify({'success': False, 'message': 'Security error'}), 403

    if not check_whitelist(ip, fingerprint):
        session.clear()
        return jsonify({'success': False, 'message': 'Security error'}), 403
    
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

@orex.route('/orex-ws/delete_record', methods=['POST'])
def delete_record():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    # Дополнительная проверка безопасности
    ip = request.remote_addr
    fingerprint = session.get('fingerprint', '')
    
    if ip != session.get('ip'):
        session.clear()
        return redirect(url_for('login'))

    if not check_whitelist(ip, fingerprint):
        session.clear()
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
    # Создаем пустые файлы безопасности при первом запуске
    for filename in [SECURITY_WHITELIST, SECURITY_BLACKLIST, LOGIN_LOG]:
        if not os.path.exists(filename):
            with open(filename, 'w', encoding='utf-8') as f:
                if filename == SECURITY_WHITELIST:
                    f.write("# Security whitelist: IP|Fingerprint|Date\n")
                elif filename == SECURITY_BLACKLIST:
                    f.write("# Security blacklist: IP|Reason|Date\n")
                elif filename == LOGIN_LOG:
                    f.write("# Login log: [Date] IP Fingerprint Status\n")
    
    orex.run(host='0.0.0.0', port=5000, debug=True)