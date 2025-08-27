#!/bin/bash

# Проверяем права
if [ "$(id -u)" -ne 0 ]; then
    echo "Этот скрипт должен запускаться от root! Используй: sudo $0"
    exit 1
fi

CONFIG_FILE="/etc/orex/.backup.conf"
SERVICE_NAME="orex-backup"

echo "=== OREX BACKUP SETUP ==="

# Спрашиваем все данные
read -p "Введите имя пользователя MariaDB: " db_user
read -sp "Введите пароль пользователя $db_user: " db_password
echo
read -p "Введите имя базы данных для бэкапа: " db_name
read -p "Куда складывать бэкапы? [/var/orex/backups]: " backup_dir
backup_dir=${backup_dir:-/var/orex/backups}

# Создаем папки
mkdir -p /etc/orex
mkdir -p "$backup_dir"
chmod 700 /etc/orex "$backup_dir"
chown root:root /etc/orex "$backup_dir"

# Сохраняем конфиг
cat > "$CONFIG_FILE" << EOF
DB_USER="$db_user"
DB_PASSWORD="$db_password"
DB_NAME="$db_name"
BACKUP_DIR="$backup_dir"
EOF

chmod 600 "$CONFIG_FILE"
echo "[+] Конфиг создан: $CONFIG_FILE"

# Создаем основной скрипт бэкапа
cat > /usr/local/bin/orex-backup.sh << 'EOF'
#!/bin/bash

# Загружаем конфиг
if [ -f /etc/orex/.backup.conf ]; then
    source /etc/orex/.backup.conf
else
    echo "Конфиг не найден! Запустите orex-backup-setup.sh"
    exit 1
fi

# Функция бэкапа
do_backup() {
    echo "[$(date)] Запуск бэкапа БД $DB_NAME"
    # Создаем временный конфиг для mysqldump
    temp_cnf=$(mktemp)
    cat > "$temp_cnf" << CONFIG
[client]
user=$DB_USER
password=$DB_PASSWORD
CONFIG
    
    backup_file="$BACKUP_DIR/${DB_NAME}_backup_$(date +\%Y-\%m-\%d_\%H-\%M-\%S).sql"
    
    if mysqldump --defaults-file="$temp_cnf" "$DB_NAME" > "$backup_file"; then
        echo "[+] Бэкап создан: $backup_file"
        # Удаляем старые бэкапы (старше 60 дней)
        find "$BACKUP_DIR" -name "*.sql" -mtime +60 -delete
    else
        echo "[-] Ошибка бэкапа!"
    fi
    
    rm -f "$temp_cnf"
}

# Проверяем права
if [ "$(id -u)" -ne 0 ]; then
    echo "Запускай от root: sudo $0"
    exit 1
fi

do_backup
EOF

chmod 700 /usr/local/bin/orex-backup.sh

# Создаем systemd таймер
cat > /etc/systemd/system/$SERVICE_NAME.timer << EOF
[Unit]
Description=Run Orex Backup every hour

[Timer]
OnCalendar=*-*-* *:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=Orex Backup Service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/orex-backup.sh
User=root
EOF

systemctl daemon-reload
systemctl enable $SERVICE_NAME.timer
systemctl start $SERVICE_NAME.timer

echo "[+] Установка завершена!"
echo "[+] Бэкап будет выполняться каждый час"
echo "[+] Проверить: systemctl status $SERVICE_NAME.timer"
EOF