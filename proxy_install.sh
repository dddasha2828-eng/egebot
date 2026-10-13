#!/usr/bin/env bash
# Прокси для Телеграма на зарубежном сервере. Ничего не хранит, только передаёт запросы.
# Запуск: curl -fsSL https://raw.githubusercontent.com/ЛОГИН/РЕПОЗИТОРИЙ/main/proxy_install.sh | bash -s -- IP_РОССИЙСКОГО_СЕРВЕРА
set -e
RU_IP="${1:?Укажи IP российского сервера с ботом}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq tinyproxy curl >/dev/null
cat > /etc/tinyproxy/tinyproxy.conf <<CONF
User tinyproxy
Group tinyproxy
Port 8888
Listen 0.0.0.0
Timeout 600
MaxClients 50
LogLevel Warning
Allow 127.0.0.1
Allow $RU_IP
ConnectPort 443
DisableViaHeader Yes
CONF
systemctl enable -q tinyproxy
systemctl restart tinyproxy
MY_IP=$(curl -s https://api.ipify.org || hostname -I | awk '{print $1}')
echo
echo "✅ Прокси готов. Пускает только сервер $RU_IP."
echo "   Адрес прокси, его нужно вставить при установке бота:"
echo
echo "   http://$MY_IP:8888"
