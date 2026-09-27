#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TUNNEL_URL="${1:-https://strength-ranging-buddhist.ngrok-free.dev}"
if [[ ! "$TUNNEL_URL" =~ ^https://[a-zA-Z0-9.-]+\.ngrok-free\.(app|dev)$ ]]; then
  printf 'Informe uma URL HTTPS ngrok-free.app ou ngrok-free.dev valida.\n' >&2
  exit 1
fi
for program in python3 ffmpeg ngrok systemctl; do
  if ! command -v "$program" >/dev/null 2>&1; then
    printf 'Falta %s. Instale-o antes de executar este script.\n' "$program" >&2
    exit 1
  fi
done
if ! systemctl --user show-environment >/dev/null 2>&1; then
  printf 'O systemd do usuario nao esta disponivel nesta sessao.\n' >&2
  exit 1
fi

python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt"
CONFIG_DIR="$HOME/.config/cc-tv"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$CONFIG_DIR" "$UNIT_DIR"
chmod 700 "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/server.env" ]]; then
  umask 077
  KEY="$("$PROJECT_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf 'CC_TV_TOKEN=%s\n' "$KEY" > "$CONFIG_DIR/server.env"
fi
chmod 600 "$CONFIG_DIR/server.env"
NGROK_BIN="$(command -v ngrok)"
cat > "$UNIT_DIR/cc-tv.service" <<EOF
[Unit]
Description=CC Tweaked TV converter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$CONFIG_DIR/server.env
ExecStart=$PROJECT_DIR/.venv/bin/python $PROJECT_DIR/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
cat > "$UNIT_DIR/cc-tv-ngrok.service" <<EOF
[Unit]
Description=Ngrok tunnel for CC Tweaked TV
After=cc-tv.service network-online.target
Wants=cc-tv.service network-online.target

[Service]
Type=simple
ExecStart=$NGROK_BIN http 8765 --url $TUNNEL_URL
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now cc-tv.service cc-tv-ngrok.service
systemctl --user restart cc-tv.service cc-tv-ngrok.service
printf '\nServicos instalados. URL: %s\n' "$TUNNEL_URL"
printf 'Chave para digitar no Minecraft (compartilhe apenas com amigos):\n'
sed -n 's/^CC_TV_TOKEN=//p' "$CONFIG_DIR/server.env"
printf '\nPara iniciar antes do login apos reiniciar o Kali, execute:\n'
printf '  sudo loginctl enable-linger %s\n' "$USER"
printf '\nPara verificar: systemctl --user status cc-tv.service cc-tv-ngrok.service\n'
