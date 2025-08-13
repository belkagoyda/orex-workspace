from flask import Flask, render_template, request, redirect, url_for, make_response, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text, create_engine
import io

orex = Flask(__name__)
orex.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://user:password@localhost/db_name'
orex.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(orex)

@orex.route('/orex-ws')
def base():
    inspector = inspect(db.engine)
    table_names = inspector.get_table_names()
    return render_template('base.html', tables=table_names)

@orex.route('/orex-ws/table', methods=['GET', 'POST'])
def show_table():
    table_name = request.args.get('name')
    
    if not table_name:
        return redirect(url_for('base'))
    
    # Получаем структуру таблицы
    inspector = inspect(db.engine)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    primary_keys = [key['name'] for key in inspector.get_pk_constraint(table_name)['constrained_columns']]
    
    # Получаем данные таблицы
    with db.engine.connect() as connection:
        result = connection.execute(text(f'SELECT * FROM `{table_name}`'))
        rows = [dict(zip(columns, row)) for row in result]
    
    # Обработка генерации ответа
    if request.method == 'POST':
        # Получаем ID выбранной записи
        row_id = request.form.get('row_id')
        
        # Находим выбранную строку
        selected_row = next((row for row in rows if str(row[primary_keys[0]]) == row_id), None)
        
        if selected_row:
            # TODO: Здесь будет реальная генерация ODT
            # Создаем фиктивный ODT-файл для демонстрации
            content = f"Сгенерированный ответ для письма {row_id}".encode('utf-8')
            
            # TODO: Реальная запись в таблицу "Исходящие"
            # with db.engine.connect() as conn:
            #     conn.execute(text(f"INSERT INTO `Исходящие` (...) VALUES (...)"))

            # Возвращаем файл для скачивания
            return send_file(
                io.BytesIO(content),
                as_attachment=True,
                download_name=f'response_{row_id}.odt',
                mimetype='application/vnd.oasis.opendocument.text'
            )
    
    return render_template('table.html', 
                          table_name=table_name,
                          columns=columns,
                          rows=rows,
                          primary_key=primary_keys[0] if primary_keys else 'id')

if __name__ == "__main__":
    orex.run(debug=True)