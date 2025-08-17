from flask import Flask, render_template, request, redirect, url_for, session, send_file
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
import os
import io

# Создаем экземпляр Flask приложения
orex = Flask(__name__)
orex.secret_key = os.urandom(24).hex()  # Автогенерация ключа

# Глобальный движок для упрощения
engine = None

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

# Новый маршрут для формы добавления записи
@orex.route('/orex-ws/vvod', methods=['GET'])
def vvod():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    table_name = request.args.get('table')
    if not table_name:
        return redirect(url_for('base'))
    
    try:
        inspector = inspect(engine)
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        primary_keys = inspector.get_pk_constraint(table_name)['constrained_columns']
        primary_key = primary_keys[0] if primary_keys else columns[0]
        
        return render_template('vvod.html', 
                              table_name=table_name, 
                              columns=columns,
                              primary_key=primary_key)
    
    except Exception as e:
        return render_template('error.html', error=str(e))

# Маршрут для сохранения новой записи
@orex.route('/orex-ws/save_record', methods=['POST'])
def save_record():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    try:
        # Получаем имя таблицы из формы
        table_name = request.form.get('table_name')
        if not table_name:
            return redirect(url_for('base'))
        
        inspector = inspect(engine)
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        primary_keys = inspector.get_pk_constraint(table_name)['constrained_columns']
        primary_key = primary_keys[0] if primary_keys else columns[0]
        
        # Собираем данные из формы
        data = {}
        for column in columns:
            # Пропускаем первичный ключ
            if column == primary_key:
                continue
                
            # Обработка разных типов данных
            if column.lower().find('галочка') != -1:
                # Для чекбоксов: если отмечен - 1, иначе - 0
                data[column] = 1 if request.form.get(column) == '1' else 0
            else:
                data[column] = request.form.get(column, '')
        
        # Строим запрос на вставку
        columns_str = ', '.join([f'`{col}`' for col in data.keys()])
        values_str = ', '.join([f':{col}' for col in data.keys()])
        insert_query = text(f"INSERT INTO `{table_name}` ({columns_str}) VALUES ({values_str})")
        
        with engine.connect() as conn:
            conn.execute(insert_query, data)
            conn.commit()
        
        # Перенаправляем обратно к таблице
        return redirect(f'/orex-ws/table?name={table_name}')
    
    except Exception as e:
        return render_template('error.html', error=str(e))

if __name__ == "__main__":
    orex.run(host='0.0.0.0', port=5000, debug=True)