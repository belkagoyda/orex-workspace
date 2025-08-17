from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from sqlalchemy import create_engine, inspect, text, MetaData, Table, insert
from sqlalchemy.exc import SQLAlchemyError
import os
import io
from datetime import datetime

# Создаем экземпляр Flask приложения
orex = Flask(__name__)
orex.secret_key = os.urandom(24).hex()  # Автогенерация ключа

# Глобальный движок для упрощения
engine = None

# Функция для получения метаданных таблицы
def get_table_metadata(table_name):
    """Получаем детальную информацию о колонках таблицы"""
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
        
        if request.method == 'POST':
            # Проверяем, какое действие выполняется
            if 'row_id' in request.form:
                # Генерация ответа на письмо
                row_id = request.form['row_id']
                selected_row = next((row for row in rows if str(row[primary_key]) == row_id), None)
                
                if selected_row:
                    # Фиктивная генерация файла
                    content = f"Ответ на письмо №{row_id}".encode('utf-8')
                    return send_file(
                        io.BytesIO(content),
                        as_attachment=True,
                        download_name=f'response_{row_id}.odt'
                    )
        
        return render_template('table.html',
                              table_name=table_name,
                              columns=columns,
                              rows=rows,
                              primary_key=primary_key)
    
    except Exception as e:
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
        # Получаем детальные метаданные вместо простого списка колонок
        columns_meta = get_table_metadata(table_name)
        
        # Фильтруем только те колонки, которые нужно показать
        visible_columns = [col for col in columns_meta if not col['autoincrement']]
        
        return render_template('vvod.html', 
                              table_name=table_name, 
                              columns=visible_columns,
                              all_columns=columns_meta)
    
    except Exception as e:
        return render_template('error.html', error=str(e))

# Полностью переписанный маршрут для сохранения записи
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
                # Если поле разрешает NULL - ставим NULL
                if col['nullable']:
                    data[col_name] = None
                # Если есть значение по умолчанию - пропускаем (БД подставит сама)
                elif col['default'] is not None:
                    continue
                # Иначе возвращаем ошибку
                else:
                    flash(f'Поле "{col_name}" обязательно для заполнения', 'danger')
                    return redirect(url_for('vvod', table=table_name))
            else:
                # Обработка чекбоксов
                if col['type'] == 'BOOLEAN' or 'галочка' in col_name.lower():
                    data[col_name] = 1 if form_value == '1' else 0
                # Обработка даты/времени
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
        
        with engine.connect() as conn:
            conn.execute(insert_query, data)
            conn.commit()
        
        flash('Запись успешно добавлена!', 'success')
        return redirect(f'/orex-ws/table?name={table_name}')
    
    except Exception as e:
        flash(f'Ошибка при сохранении: {str(e)}', 'danger')
        return redirect(url_for('vvod', table=table_name))

if __name__ == "__main__":
    orex.run(host='0.0.0.0', port=5000, debug=True)