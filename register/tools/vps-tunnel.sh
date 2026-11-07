#!/usr/bin/env bash
# vps-tunnel — BRD-релей дома + SSH-проброс порта на VPS.
#
# Зачем: Bright Data привязан к IP домашней машины, с VPS прокси не работает.
# Релей на домашней машине добавляет авторизацию BRD, SSH-туннель отдаёт его
# VPS'у как http://127.0.0.1:18888 (обычный HTTP-прокси без пароля).
#
#   bash tools/vps-tunnel.sh start|stop|status
#
# VPS задаётся переменной окружения: VPS=user@host bash tools/vps-tunnel.sh start
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
VPS="${VPS:?set VPS=user@host}"
PORT="${PORT:-18888}"
DATA="$ROOT/data"
mkdir -p "$DATA"
RELAY_PID="$DATA/relay.pid"
TUNNEL_PID="$DATA/tunnel.pid"
RELAY_LOG="$DATA/relay.log"
TUNNEL_LOG="$DATA/tunnel.log"

relay_alive() {
  [ -f "$RELAY_PID" ] && kill -0 "$(cat "$RELAY_PID" 2>/dev/null)" 2>/dev/null
}

tunnel_alive() {
  [ -f "$TUNNEL_PID" ] && kill -0 "$(cat "$TUNNEL_PID" 2>/dev/null)" 2>/dev/null
}

start() {
  if relay_alive; then echo "relay уже работает (pid $(cat "$RELAY_PID"))"; else
    nohup env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH \
      python3 "$HERE/relay_service.py" --port "$PORT" >> "$RELAY_LOG" 2>&1 &
    echo $! > "$RELAY_PID"
    sleep 1
    echo "relay: pid $(cat "$RELAY_PID") → 127.0.0.1:$PORT"
  fi
  if tunnel_alive; then echo "туннель уже работает (pid $(cat "$TUNNEL_PID"))"; else
    nohup bash -c 'while true; do
        ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
            -o ServerAliveCountMax=3 -R '"$PORT"':127.0.0.1:'"$PORT"' '"$VPS"'
        sleep 5
      done' >> "$TUNNEL_LOG" 2>&1 &
    echo $! > "$TUNNEL_PID"
    sleep 2
    echo "туннель: pid $(cat "$TUNNEL_PID") → $VPS:$PORT"
  fi
  echo "проверка с VPS:"
  ssh -o BatchMode=yes "$VPS" "curl -s -m 20 -x http://127.0.0.1:$PORT https://api.ipify.org || echo FAIL"
}

stop() {
  for f in "$TUNNEL_PID" "$RELAY_PID"; do
    if [ -f "$f" ] && kill -0 "$(cat "$f" 2>/dev/null)" 2>/dev/null; then
      kill "$(cat "$f")" 2>/dev/null
      echo "остановлен $(basename "$f" .pid) (pid $(cat "$f"))"
    fi
    rm -f "$f"
  done
  pkill -f "ssh .* -R $PORT:127.0.0.1:$PORT $VPS" 2>/dev/null || true
}

status() {
  relay_alive && echo "relay: ok (pid $(cat "$RELAY_PID"))" || echo "relay: нет"
  tunnel_alive && echo "tunnel: ok (pid $(cat "$TUNNEL_PID"))" || echo "tunnel: нет"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$VPS" \
    "curl -s -m 20 -x http://127.0.0.1:$PORT https://api.ipify.org || echo VPS-PROXY-FAIL"
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: bash tools/vps-tunnel.sh start|stop|status"; exit 2 ;;
esac
