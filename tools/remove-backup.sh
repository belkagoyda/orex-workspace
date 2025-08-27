#!/bin/bash

# Проверяем права
if [ "$(id -u)" -ne 0 ]; then
    echo "Этот скрипт должен запускаться от root! Используй: sudo $0"
    exit 1
fi

SERVICE_NAME="orex-backup"

echo "=== УДАЛЕНИЕ OREX BACKUP ==="

# Останавливаем и удаляем таймер
systemctl stop $SERVICE_NAME.timer 2>/dev/null
systemctl disable $SERVICE_NAME.timer 2>/dev/null
rm -f /etc/systemd/system/$SERVICE_NAME.service
rm -f /etc/systemd/system/$SERVICE_NAME.timer
systemctl daemon-reload

# Удаляем скрипт
rm -f /usr/local/bin/orex-backup.sh

# Удаляем конфиг (ОСТОРОЖНО! Это удалит все настройки)
read -p "Удалить конфиг с паролями? (y/N): " confirm
if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
    rm -f /etc/orex/.backup.conf
    echo "[+] Конфиг удален"
fi

# Очищаем бэкапы (опционально)
read -p "Удалить все бэкапы? (y/N): " confirm_backups
if [ "$confirm_backups" = "y" ] || [ "$confirm_backups" = "Y" ]; then
    if [ -f /etc/orex/.backup.conf ]; then
        source /etc/orex/.backup.conf 2>/dev/null
        if [ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
            rm -rf "$BACKUP_DIR"
            echo "[+] Бэкапы удалены: $BACKUP_DIR"
        fi
    fi
fi

echo "[+] Удаление завершено!"