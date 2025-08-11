1. Создание виртуального окружения в VSCodium (команды для терминала самого VSCodium)
1.1. Создание окружения:
    python -m venv venv
// папка venv появится в вашем проекте

1.2. Активация окружения:
    source venv/bin/activate

1.3. Установка библиотек, фреймворков:
    pip install flask

Чтобы VSCodium распознал окружение:
    Нажмите Ctrl+Shift+P
    Выберите Python: Select Interpreter
    Найдите путь к интерпретатору в папке venv (например, ./venv/bin/python или .\venv\Scripts\python.exe)
Готово! Теперь все зависимости будут изолированы в venv, а VSCodium будет использовать это окружение.

КАЖДЫЙ РАЗ ПЕРЕД РАБОТОЙ:
    source venv/bin/activate

Git:
1. git clone https://github.com/belkagoyda/orex-workspace
2. Слева выбрать Source Control, нажать Commit + Push
3. Перейти на гитхаб, авторизоваться под собой, получить доступ