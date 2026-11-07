# -*- coding: utf-8 -*-
"""limits — квоты Perch по аккаунтам.

У Perch нет API-ключей и серверных «квот по ключу». Есть план:
  * Starter (free): 20 000 PT включённого использования в месяц
    ($20, PT = Perch Tokens);
  * Pro / Ultra — платные, здесь не используются.

Расход считаем из своего леджера моста (`data/usage.jsonl`, пишет perch2api)
и из последнего пробного тура (`input_tokens/output_tokens` в accounts.jsonl).
Модуль отдаёт панели ту же форму, что и шаблон (`summary`, `model_costs`,
`collect`, `usage_detailed`), но без походов на сервер.
"""
import json
import os
import threading
import time

from . import config

LIMITS_FILE = os.path.join(config.DATA_DIR, "limits.json")
_lock = threading.Lock()

CACHE_TTL = int(getattr(config, "LIMITS_CACHE_TTL", 600))
FREE_PT = int(getattr(config, "FREE_PT_PER_MONTH", 20000) or 20000)


def _load_cache():
    try:
        with open(LIMITS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"accounts": {}, "checked_at": None}


def _save_cache(data):
    tmp = f"{LIMITS_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LIMITS_FILE)


def usage_detailed():
    """Леджер моста perch2api: email → {requests, input_tokens, output_tokens, pt}."""
    path = os.path.join(config.DATA_DIR, "usage.jsonl")
    out = {}
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                key = rec.get("email") or rec.get("key")
                if not key:
                    continue
                cur = out.setdefault(key, {"requests": 0, "input_tokens": 0,
                                           "output_tokens": 0, "pt": 0.0,
                                           "last_ts": None})
                cur["requests"] += 1
                cur["input_tokens"] += int(rec.get("input_tokens") or 0)
                cur["output_tokens"] += int(rec.get("output_tokens") or 0)
                cur["pt"] += float(rec.get("pt") or 0.0)
                cur["last_ts"] = rec.get("ts") or cur["last_ts"]
    except OSError:
        pass
    return out


def collect(accounts, proxy=None, only_missing=False, max_workers=3,
            on_progress=None):
    """Локальная сводка по аккаунтам в data/limits.json (без логинов)."""
    accounts = accounts or []
    usage = usage_detailed()
    cache = _load_cache()
    quotas = cache.setdefault("accounts", {})
    now = time.time()
    for i, a in enumerate(accounts, 1):
        email = a.get("email")
        if not email:
            continue
        u = usage.get(email) or {}
        quotas[email] = {
            "email": email,
            "plan": "starter",
            "free_pt_per_month": FREE_PT,
            "pt_used_est": round(float(u.get("pt") or 0.0), 2),
            "requests": u.get("requests", 0),
            "input_tokens": u.get("input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
            "live_ok": a.get("live_ok"),
            "checked_at": now,
            "note": "Starter free: $20 (20 000 PT) включённого использования/мес",
        }
        if on_progress and i % 10 == 0:
            on_progress(i, len(accounts))
    cache["checked_at"] = now
    with _lock:
        _save_cache(cache)
    return cache


def model_costs(accounts=None):
    """Starter-модели; точных PT-цен в открытом виде нет."""
    return {
        "models": [{"model": m, "usd": 0.0, "requests": 0}
                   for m in (config.get("models") or [])],
        "note": ("Perch Starter: $0, 20 000 PT включённого использования в месяц. "
                 "Модели: Laguna S 2.1, DeepSeek V4.1 Flash, GLM 5.3 Flash, "
                 "Qwen 3.8 Max. Расход считается мостом perch2api."),
    }


def summary(accounts=None):
    """Сводка для панели: форма та же, что у шаблона."""
    from .results import load_accounts
    accounts = accounts if accounts is not None else load_accounts()
    cache = _load_cache()
    quotas = cache.get("accounts", {})
    usage = usage_detailed()

    rows = []
    total_requests = total_in = total_out = 0
    total_pt = 0.0
    alive = 0
    for a in accounts:
        email = a.get("email")
        q = quotas.get(email) or {}
        u = usage.get(email) or {}
        live = bool(a.get("live_ok") or q.get("live_ok"))
        alive += 1 if live else 0
        req = u.get("requests", 0)
        tin = u.get("input_tokens", 0)
        tout = u.get("output_tokens", 0)
        pt = u.get("pt", 0.0)
        total_requests += req
        total_in += tin
        total_out += tout
        total_pt += pt
        rows.append({
            "email": email,
            "plan": "starter",
            "free": True,
            "session_alive": live,
            "pt_used": pt,
            "pt_limit": FREE_PT,
            "used_pct": round(100.0 * pt / FREE_PT, 2) if FREE_PT else None,
            "req_used": req or None,
            "local_requests": req,
            "prompt_tokens": tin,
            "completion_tokens": tout,
            "total_tokens": tin + tout,
            "usd": round(pt / 1000.0, 4),   # 20 000 PT ≈ $20 → 1 PT = $0.001
            "models": [],
            "reset_at": None,
            "exhausted": bool(FREE_PT and pt >= FREE_PT),
            "error": None,
            "checked_at": q.get("checked_at"),
            "note": q.get("note"),
        })

    return {
        "accounts": rows,
        "totals": {
            "accounts": len(rows),
            "free_accounts": len(rows),
            "exhausted": sum(1 for r in rows if r["exhausted"]),
            "alive": alive,
            "requests": total_requests,
            "prompt_tokens": total_in,
            "completion_tokens": total_out,
            "total_tokens": total_in + total_out,
            "usd": round(total_pt / 1000.0, 4),
            "usd_per_account_est": FREE_PT / 1000.0,   # $20 на аккаунт
            "usd_remaining_est": round(max(0.0, FREE_PT * len(rows) - total_pt)
                                       / 1000.0, 4) if rows else None,
            "tokens_remaining_est": None,
            "avg_usd_per_1k_tokens": None,
            "spent_out_local": 0,
            "local_ledger": True,
        },
        "models_available": {},
        "billing": {
            "unit": "PT (Perch Tokens), 1 PT ≈ $0.001",
            "period": "месяц",
            "on_exhausted": "Starter-лимит исчерпан до конца периода",
            "note": ("Perch Starter: $0 без карты, $20 (20 000 PT) включённого "
                     "использования в месяц. API-ключей нет — мост работает "
                     "через CLI-сессию аккаунта."),
        },
        "checked_at": cache.get("checked_at"),
    }
