#!/bin/bash
# Бесконечный держатель SSH-туннеля VPS:18888 → home:18888 (BRD-релей).
VPS="${VPS:?set VPS=user@host}"
PORT="${PORT:-18888}"
while true; do
  ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
      -o ServerAliveCountMax=3 -R "$PORT:127.0.0.1:$PORT" "$VPS"
  sleep 5
done
