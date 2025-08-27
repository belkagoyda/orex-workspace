#!/bin/bash
sudo openssl genrsa -out orex.key 2048
sudo openssl req -new -x509 -key orex.key -out orex.crt -days 365
sudo cp orex.key /etc/ssl/private/
sudo cp orex.crt /etc/ssl/certs/
echo "SSL ключ и сертификат созданы и загружены"

ls -la /etc/ssl/private/orex.key
ls -la /etc/ssl/certs/orex.crt

sudo cp -r /etc/apache2/ ~/apache2_backup/
echo "Бэкап существующей папки apache2 создан"

sudo cp orex-ssl.conf /etc/apache2/sites-available/
sudo a2ensite orex-ssl.conf
sudo a2enmod ssl proxy proxy_http
sudo systemctl reload apache2
echo "Конфиг orex-ssl загружен, Apache перезагружен"