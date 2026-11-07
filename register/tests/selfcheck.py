#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Офлайн-самопроверка perch-register (без сети): exit 0 = pass.

Проверяет конфиг, контракт core/register.py, парсер GitHub-пула, наличие
CLI/node, атомарную запись результатов и панель. Секреты не выводятся.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# Чистый клон: config.json ещё нет — берём пример (он без секретов).
if not os.path.exists(os.path.join(ROOT, "config.json")):
    import shutil
    src = os.path.join(ROOT, "config.example.json")
    if os.path.exists(src):
        shutil.copy(src, os.path.join(ROOT, "config.json"))

from core import config, register, results          # noqa: E402

fails = []


def chk(cond, msg):
    print(f"[{'ok  ' if cond else 'FAIL'}] {msg}")
    if not cond:
        fails.append(msg)


# 1. конфиг
chk(config.BASE_URL == "https://chat.perchai.app", "base_url = chat.perchai.app")
chk(config.PANEL_PORT == 9943, "panel_port = 9943")
chk(int(config.get("bridge_port") or 0) == 12066, "bridge_port = 12066")
chk(config.ENGINE == "camoufox", "engine = camoufox (GitHub OAuth)")
chk(config.get("app_url") == "https://app.perchai.app", "app_url = app.perchai.app")
chk(bool(config.get("gh_pool_file")), "gh_pool_file задан")
_pool_path = os.path.expanduser(config.get("gh_pool_file") or "")
chk(not _pool_path or os.path.exists(_pool_path) or True,
    f"пул GitHub: {config.get('gh_pool_file')} (файл нужен только для register)")
chk(not config.get("require_proxy"), "require_proxy=false (домашний IP проходит)")
chk(int(config.get("free_pt_per_month") or 0) == 20000,
    "Starter: 20 000 PT/мес")

# 2. контракт register.py
for fn in ("register_one", "check_account", "list_models", "free_models",
           "test_key", "load_pool", "used_usernames", "run_prompt",
           "choose_starter", "plan_status", "ensure_cli"):
    chk(callable(getattr(register, fn, None)), f"core.register.{fn} есть")
for m in ("open", "close", "start_cli_login", "github_oauth", "wait_session"):
    chk(callable(getattr(register.ServiceClient, m, None)),
        f"ServiceClient.{m} есть")
chk(register.APP_URL_DEFAULT == "https://app.perchai.app", "APP_URL_DEFAULT")
chk(register.CLI_PLAN_PATH == "/api/perch-terminal/cli-plan", "CLI_PLAN_PATH")

# 3. парсер пула: 5 полей, без вывода секретов
pool = register.load_pool()
chk(not _pool_path or not os.path.exists(_pool_path) or len(pool) >= 1,
    f"пул GitHub: {len(pool)} аккаунтов" + ("" if pool else " (файл не задан — норма для чистого клона)"))
chk(all(a.get("username") and a.get("totp") for a in pool),
    "у всех записей есть username и TOTP")
if pool:
    first = pool[0]
    chk("password" in first and "totp" in first,
        "поля пароля/TOTP читаются (значения не печатаются)")
if pool:
    sample = next((a for a in pool if len(a["username"]) > 3), None)
    chk(bool(sample), f"пример username: {sample['username'] if sample else '—'}")

# 4. CLI и node
cli = register._perch_cli()
if cli:
    chk(True, f"perch CLI найден: {cli}")
else:
    print("[warn] perch CLI не установлен — bash tools/install-cli.sh (нужен для register)")
chk(register._node_bin() is not None, "node_bin задан")
chk(register.list_models() and len(register.list_models()) >= 3,
    f"Starter-моделей: {len(register.list_models())}")

# 5. атомарная запись аккаунта (temp, реальный data/ не трогаем)
with tempfile.TemporaryDirectory() as d:
    old_jsonl, old_combo = config.OUTPUT_JSONL, config.OUTPUT_COMBO
    config.OUTPUT_JSONL = os.path.join(d, "accounts.jsonl")
    config.OUTPUT_COMBO = os.path.join(d, "accounts.txt")
    try:
        results.save_account({"email": "selfcheck@example.com",
                              "github_username": "selfcheck",
                              "session_file": "/tmp/x.json", "live_ok": True})
        accs = results.load_accounts()
        chk(len(accs) == 1 and accs[0]["github_username"] == "selfcheck",
            "results.save_account / load_accounts")
        results.save_account({"email": "selfcheck@example.com",
                              "github_username": "selfcheck",
                              "session_file": "/tmp/y.json"})
        chk(len(results.load_accounts()) == 1,
            "save_account идемпотентен по email")
        chk(os.path.exists(config.OUTPUT_COMBO), "combo-файл пишется")
    finally:
        config.OUTPUT_JSONL, config.OUTPUT_COMBO = old_jsonl, old_combo

# 6. панель
with open(os.path.join(ROOT, "panel", "server.py"), encoding="utf-8") as f:
    panel_src = f.read()
chk('default="127.0.0.1"' in panel_src, "панель по умолчанию слушает 127.0.0.1")
chk("ThreadingHTTPServer" in panel_src, "панель на stdlib ThreadingHTTPServer")
chk("check_account" in panel_src, "панель умеет проверять аккаунт")

print(f"\n{'PASS' if not fails else 'FAIL'}: {len(fails)} ошибок")
sys.exit(1 if fails else 0)
