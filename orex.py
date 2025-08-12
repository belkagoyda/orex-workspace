from flask import Flask, render_template#, url_for
from flask_sqlalchemy import SQLAlchemy

orex = Flask(__name__)
orex.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blod.db'

@orex.route('/orex-ws')
def base():
    return "Выбор таблиц"

@orex.route('/orex-ws/table')
def table():
        return "Таблица"
if __name__ == "__main__":
    orex.run(debug=True)
    