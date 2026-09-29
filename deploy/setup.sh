#!/usr/bin/env bash
# One-time provisioning for a fresh Lightsail Ubuntu instance. Run via SSH
# as the default `ubuntu` sudo-capable user:
#
#   curl -fsSL https://raw.githubusercontent.com/KevinSuasnabar/pokemon-scrapping-bot/main/deploy/setup.sh | bash
#
# Re-running it later (e.g. after a `git push`) just pulls and reinstalls —
# it does not touch .env or restart the service on its own.
#
# What this script deliberately does NOT do:
#   - install/enable the systemd service (needs .env in place first)
#   - create or edit .env (Telegram credentials are yours to place there)
set -euo pipefail

APP_DIR=/opt/pokemon-tracker
REPO_URL="https://github.com/KevinSuasnabar/pokemon-scrapping-bot.git"

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git

if ! id tracker &>/dev/null; then
    sudo useradd --system --no-create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin tracker
fi

if [ -d "$APP_DIR/.git" ]; then
    sudo -u tracker git -C "$APP_DIR" pull
else
    sudo mkdir -p "$APP_DIR"
    sudo chown tracker:tracker "$APP_DIR"
    sudo -u tracker git clone "$REPO_URL" "$APP_DIR"
fi

sudo -u tracker python3 -m venv "$APP_DIR/.venv"
sudo -u tracker "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u tracker "$APP_DIR/.venv/bin/pip" install -e "$APP_DIR[ripley]"

# Ripley now requires a real browser (Cloudflare JS challenge, confirmed
# live 2026-09-29 — see ripley.py's PlaywrightTransport). Chromium's OS-level
# shared libraries need root; the browser binary itself installs separately,
# into the `tracker` user's own cache dir, so the systemd service (which
# runs as `tracker`) can find it at runtime.
sudo "$APP_DIR/.venv/bin/playwright" install-deps chromium
sudo -u tracker "$APP_DIR/.venv/bin/playwright" install chromium

sudo cp "$APP_DIR/deploy/tracker.service" /etc/systemd/system/tracker.service
sudo systemctl daemon-reload

echo
echo "Provisioning listo. Pasos que faltan (manuales, con tus credenciales):"
echo "  1. sudo nano $APP_DIR/.env"
echo "     TELEGRAM_BOT_TOKEN=..."
echo "     TELEGRAM_CHAT_ID=..."
echo "  2. sudo chown tracker:tracker $APP_DIR/.env && sudo chmod 600 $APP_DIR/.env"
echo "  3. sudo systemctl enable --now tracker"
echo "  4. journalctl -u tracker -f   # para ver que este corriendo bien"
