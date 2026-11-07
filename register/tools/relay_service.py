#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""relay_service — постоянный BRD-релей на фиксированном порту (для VPS).

Зачем: Bright Data авторизует по IP — с домашней машины зоны работают, с VPS
нет. Поднимаем этот релей ДОМА (он добавляет Proxy-Authorization к BRD) и
пробрасываем его порт на VPS по SSH:

  home:  python3 tools/relay_service.py --port 18888
  home:  ssh -N -R 18888:127.0.0.1:18888 user@your-vps
  vps:   camoufox ходит на http://127.0.0.1:18888 (без авторизации)

Управление обоими процессами — `bash tools/vps-tunnel.sh start|stop|status`.
"""
import argparse
import json
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core.proxy_relay import Relay  # noqa: E402


def pick_upstream(zone=None, country=None, proxy_file=None):
    path = proxy_file or os.environ.get("PROXY_JSON") or os.path.join(ROOT, "proxy.json")
    d = json.load(open(path, encoding="utf-8"))
    if d.get("url"):
        return d["url"]
    prov = (d.get("providers") or {}).get("brightdata") or {}
    zones = prov.get("zones") or []
    if zone:
        zones = [z for z in zones if z.get("zone") == zone]
    if country:
        zones = [z for z in zones if (z.get("country") or "").lower() == country.lower()]
    if not zones:
        raise SystemExit(f"нет BRD-зоны под фильтр zone={zone} country={country}")
    z = zones[0]
    return ("http://brd-customer-%s-zone-%s-country-%s:%s@%s" %
            (prov.get("customer"), z.get("zone"), z.get("country"),
             z.get("password"), prov.get("host")))


def main():
    ap = argparse.ArgumentParser(description="Постоянный BRD-релей для VPS")
    ap.add_argument("--port", type=int, default=18888)
    ap.add_argument("--upstream", default=None, help="полная строка прокси (перебивает proxy.json)")
    ap.add_argument("--zone", default=None)
    ap.add_argument("--country", default=None)
    ap.add_argument("--proxy-file", default=None, help="proxy.json с зонами (или PROXY_JSON)")
    args = ap.parse_args()

    upstream = args.upstream or pick_upstream(args.zone, args.country, args.proxy_file)
    relay = Relay(upstream, port=args.port)
    print(f"relay {relay.url} → {upstream.split('@')[-1]}", flush=True)

    stop = {"v": False}

    def _term(*_a):
        stop["v"] = True

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    while not stop["v"]:
        time.sleep(1)
    relay.close()
    print("relay stopped", flush=True)


if __name__ == "__main__":
    sys.exit(main())
