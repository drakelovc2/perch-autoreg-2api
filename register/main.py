#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perch-register — CLI автореггера Perch (chat.perchai.app, GitHub OAuth).

Запуск на этой машине (ОБЯЗАТЕЛЬНО):
  env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH python3 main.py <cmd>

Команды:
  register [--github USER]    один аккаунт end-to-end
  batch N                     пачка (circuit breaker, запись после каждого)
  pool                        состояние GitHub-пула (без секретов)
  accounts                    сохранённые аккаунты
  check EMAIL                 проверить план/сессию аккаунта
  models                      Starter-модели
  check-proxies               проверка прокси

Панель (порт 9943) поднимается ТОЛЬКО через `bash start.sh`.
"""
import argparse
import sys
import time

from core import config
from core import proxy as proxy_mod
from core import results
from core.register import (register_one, check_account, list_models,
                           load_pool, used_usernames, _perch_cli)


def _rotator():
    static = proxy_mod.load_all()
    alive = []
    if static:
        alive, _ = proxy_mod.check_all(static)
    rot = proxy_mod.make_rotator(alive)
    return rot if len(rot) else None


def _pick_proxy():
    rot = _rotator()
    if not rot:
        return None
    px = rot.next()
    print(f"  прокси: {proxy_mod.mask(px)}")
    return px


def _print_account(acc):
    print(f"  github:   {acc.get('github_username')}")
    print(f"  email:    {acc.get('email')}")
    print(f"  user_id:  {acc.get('user_id')}")
    print(f"  plan:     {acc.get('plan')} (required={acc.get('plan_required')})")
    print(f"  live:     {acc.get('live_ok')}  {acc.get('live_answer') or ''}")
    if acc.get("live_model"):
        print(f"  модель:   {acc['live_model']}  "
              f"in/out tokens: {acc.get('input_tokens')}/{acc.get('output_tokens')}")
    print(f"  сессия:   {acc.get('session_file')}")


def _save_pending(acc, status):
    try:
        results.save_pending({**acc, "status": status})
        print(f"  креды сохранены → data/pending.jsonl ({status})")
    except Exception as exc:
        print(f"  [!] pending не записан: {exc}")


def cmd_register(args):
    gh = None
    if args.github:
        gh = next((a for a in load_pool() if a["username"] == args.github), None)
        if not gh:
            print(f"github {args.github} нет в пуле")
            return 1
    acc = register_one(github=gh, proxy=_pick_proxy())
    if acc.get("error"):
        print(f"✗ {acc['error']}")
        print(f"  github: {acc.get('github_username')}  email: {acc.get('email')}")
        _save_pending(acc, "error")
        return 1
    results.save_account(acc)
    _print_account(acc)
    return 0


def cmd_batch(args):
    rot = _rotator()
    ok, fails = 0, 0
    for i in range(1, args.count + 1):
        print(f"\n── [{i}/{args.count}] ──────────────────────────", flush=True)
        acc = register_one(proxy=rot.next() if rot else None)
        if acc.get("error"):
            print(f"✗ {acc['error']}", flush=True)
            print(f"  github: {acc.get('github_username')}", flush=True)
            _save_pending(acc, "error")
            fails += 1
            if fails >= config.CIRCUIT_BREAKER_FAILS:
                print(f"\n⛔ circuit breaker: {fails} фейлов подряд — стоп")
                break
        else:
            results.save_account(acc)
            ok += 1
            fails = 0
            _print_account(acc)
        if i < args.count:
            time.sleep(config.BATCH_INTERVAL_SEC)
    print(f"\n══ Итог: {ok}/{args.count} аккаунтов → {config.OUTPUT_JSONL}")
    return 0 if ok else 1


def cmd_pool(_args):
    pool = load_pool()
    used = used_usernames()
    print(f"CLI: {_perch_cli() or 'НЕ НАЙДЕН (bash tools/install-cli.sh)'}")
    print(f"Пул GitHub: {len(pool)} аккаунтов, занято {len(used & {a['username'] for a in pool})}")
    for a in pool:
        mark = "×" if a["username"] in used else "·"
        print(f"  {mark} {a['pool_index']:>2}  {a['username']}")
    return 0


def cmd_accounts(_args):
    accs = results.load_accounts()
    print(f"Аккаунтов: {len(accs)} ({config.OUTPUT_JSONL})")
    for a in accs:
        print(f"  {a.get('email')}  gh={a.get('github_username')}  "
              f"live={a.get('live_ok')}  plan={a.get('plan')}  {a.get('created_at')}")
    return 0


def cmd_check(args):
    accs = results.load_accounts()
    acc = next((a for a in accs if a.get("email") == args.email
                or a.get("github_username") == args.email), None)
    if not acc:
        print(f"аккаунт {args.email} не найден")
        return 1
    res = check_account(acc, proxy=_pick_proxy())
    if res.get("ok"):
        print(f"✓ {acc.get('email')}: plan_required={res.get('plan_required')}")
        return 0
    print(f"✗ {acc.get('email')}: {res.get('error')}")
    return 1


def cmd_models(_args):
    print("Starter-модели Perch (free, $20 включённого использования/мес):")
    for m in list_models():
        print(f"  {m}")
    return 0


def cmd_check_proxies(_args):
    proxies = proxy_mod.load_all()
    if not proxies:
        print("Прокси не заданы — флоу ходит напрямую")
        return 0
    alive, dead = proxy_mod.check_all(proxies)
    print(f"✓ {len(alive)} живых, ✗ {len(dead)} мёртвых")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="perch-register CLI")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("register", help="один аккаунт end-to-end")
    p.add_argument("--github", help="username из пула (иначе первый свободный)")
    p.set_defaults(fn=cmd_register)
    p = sub.add_parser("batch", help="пачка аккаунтов")
    p.add_argument("count", type=int)
    p.set_defaults(fn=cmd_batch)
    p = sub.add_parser("pool", help="состояние GitHub-пула")
    p.set_defaults(fn=cmd_pool)
    p = sub.add_parser("accounts", help="сохранённые аккаунты")
    p.set_defaults(fn=cmd_accounts)
    p = sub.add_parser("check", help="проверить аккаунт")
    p.add_argument("email")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("models", help="Starter-модели")
    p.set_defaults(fn=cmd_models)
    p = sub.add_parser("check-proxies", help="проверка прокси")
    p.set_defaults(fn=cmd_check_proxies)
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
