#!/usr/bin/env bash
# Установка и обновление бота на сервере (Ubuntu).
# Запуск: curl -fsSL https://raw.githubusercontent.com/ЛОГИН/РЕПОЗИТОРИЙ/main/install.sh | sudo bash -s -- ЛОГИН/РЕПОЗИТОРИЙ
# Повторный запуск той же команды обновляет бота до последней версии с GitHub.
set -e

REPO="${1:?Укажи репозиторий, например dasha/ege-bot}"
APP=/opt/ege_bot
DATA=/opt/ege_bot_data
ENVF=/etc/ege_bot.env
SVC=ege-bot

echo "🌿 Устанавливаю нужные программы..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null

echo "🌿 Скачиваю код бота с GitHub..."
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch -q origin
  git -C "$APP" reset -q --hard origin/HEAD
else
  rm -rf "$APP"
  git clone -q "https://github.com/$REPO.git" "$APP"
fi

echo "🌿 Ставлю библиотеки..."
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

mkdir -p "$DATA"

if [ ! -f "$ENVF" ]; then
  echo
  printf "🔑 Вставь токен бота из BotFather и нажми Enter: "
  read -r TOKEN < /dev/tty
  printf "🌐 Адрес прокси для Телеграма (если нет, просто нажми Enter): "
  read -r PROXY < /dev/tty
  cat > "$ENVF" <<EOF
BOT_TOKEN=$TOKEN
DATA_DIR=$DATA
AUTO_ACCESS=0
TELEGRAM_PROXY=$PROXY
EOF
  chmod 600 "$ENVF"
fi

cat > /etc/systemd/system/$SVC.service <<EOF
[Unit]
Description=EGE math club bot
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=$ENVF
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable -q $SVC
systemctl restart $SVC
sleep 4

if systemctl is-active -q $SVC; then
  echo
  echo "✅ Бот запущен и будет работать сам, даже после перезагрузки сервера."
  echo "   Открой бота в Телеграме и нажми /start первой, так ты станешь админом."
else
  echo
  echo "❌ Бот не запустился. Скопируй текст ниже и пришли Claude:"
  journalctl -u $SVC -n 30 --no-pager
fi
