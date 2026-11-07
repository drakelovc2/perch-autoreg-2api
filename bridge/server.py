#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perch2api — OpenAI-совместимый шлюз к Perch (chat.perchai.app) по CLI-сессиям.

Аккаунты берутся из реггера `register/data/accounts.jsonl`
(поля account_dir/session_file/live_ok). На запрос запускается официальный
`perch run "<prompt>" --json --mode ask` с `PERCH_CLI_AUTH_DIR` нужного
аккаунта и свежим cwd (threadId у Perch выводится из cwd → каждый запрос —
новый тред, история не утекает между запросами).

Эндпоинты:
  GET  /health                 пул: живые/кулдаун/в полёте
  GET  /v1/models              Starter-модели
  POST /v1/chat/completions    OpenAI-совместимый (stream=true поддержан
                               псевдо-стримом: ответ отдаётся после тура)

Особенности Perch:
  * API-ключей нет — авторизация CLI-сессией (Supabase JWT + refresh);
    CLI сам обновляет токен при запуске.
  * Один тур ~6–8k входных токенов (системный контекст Perch) → недешёво;
    Starter: $20 (20 000 PT) включённого использования в месяц.
  * Отсюда латентность: Node-старт + агентный тур ≈ 15–40 с. Это не чат
    для интерактива, а батч/фон.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
DATA_DIR = os.path.join(ROOT, "data")

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 12066,
    "api_key": "",
    "accounts_file": os.path.join(
        ROOT, "..", "register", "data", "accounts.jsonl"),
    "perch_cli": os.path.join(
        ROOT, "..", "register", ".cli", "node_modules", ".bin", "perch"),
    "node_bin": "node",
    "timeout_sec": 300,
    "max_concurrency": 3,
    "cooldown_sec": 600,
    "max_prompt_chars": 24000,
    "models": ["perch-auto", "deepseek-flash-starter", "glm-5.3-flash-starter", "crouton-alpha", "gemini-4-argon", "claude-opus-5.5", "gpt-6.1-sol", "gpt-6.1-terra", "kimi-3.1-pro", "hy4", "deepseek-v4.1-flash-1017", "gpt-6-luna", "claude-sonnet-5.5", "claude-haiku-5.5", "kimi-3", "grok-4.7", "grok-4.6", "glm-5.3", "glm-5.3-flash", "qwen-3.8-flash", "qwen-3.8-max", "qwen-3.8", "minimax-m3", "nemotron-ultra", "nemotron-lightning", "deepseek-v4", "deepseek-v4-flash-0731-262k", "qwen-3.7-plus", "inkling", "beam-1.0", "kimi-2.7", "glm-5.2", "gpt-6-sol", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
    "usage_log": os.path.join(DATA_DIR, "usage.jsonl"),
}


def _load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.load(open(CONFIG_PATH, encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


CFG = _load_config()
os.makedirs(DATA_DIR, exist_ok=True)
CFG["accounts_file"] = os.path.abspath(CFG["accounts_file"])
CFG["perch_cli"] = os.path.abspath(CFG["perch_cli"])
CFG["usage_log"] = os.path.abspath(CFG["usage_log"])

_lock = threading.Lock()
_usage_lock = threading.Lock()


# ── пул аккаунтов ────────────────────────────────────────────────────────

class Account:
    def __init__(self, rec):
        self.email = rec.get("email") or "?"
        self.github = rec.get("github_username")
        self.dir = rec.get("account_dir") or ""
        self.live = rec.get("live_ok")
        self.inflight = 0
        self.cool_until = 0.0
        self.fails = 0
        self.requests = 0
        self.tokens_in = 0
        self.tokens_out = 0

    @property
    def session_file(self):
        return os.path.join(self.dir, "auth", "cli-auth-session.json")

    def usable(self):
        if not self.dir or not os.path.exists(self.session_file):
            return False
        if self.live is False:
            return False
        return time.time() >= self.cool_until


POOL = []
POOL_MTIME = 0.0


def reload_pool(force=False):
    global POOL, POOL_MTIME
    try:
        mtime = os.path.getmtime(CFG["accounts_file"])
    except OSError:
        return
    if not force and mtime == POOL_MTIME:
        return
    forced = {a.email: a for a in POOL}
    new = []
    try:
        with open(CFG["accounts_file"], encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not rec.get("account_dir"):
                    continue
                acc = forced.get(rec.get("email")) or Account(rec)
                acc.dir = rec.get("account_dir") or acc.dir
                acc.live = rec.get("live_ok", acc.live)
                new.append(acc)
    except OSError:
        return
    POOL = new
    POOL_MTIME = mtime


def pick_account(exclude=()):
    with _lock:
        cands = [a for a in POOL if a.usable() and a.email not in exclude]
        if not cands:
            return None
        acc = min(cands, key=lambda a: (a.inflight, a.requests))
        acc.inflight += 1
        return acc


def release_account(acc, ok, err=""):
    with _lock:
        acc.inflight = max(0, acc.inflight - 1)
        acc.requests += 1
        if ok:
            acc.fails = 0
            acc.cool_until = 0.0
            return
        acc.fails += 1
        low = (err or "").lower()
        if any(k in low for k in ("usage limit", "quota", "capacity",
                                  "monthly allowance", "starter_model_blocked",
                                  "plan_model_blocked", "429")):
            acc.cool_until = time.time() + int(CFG["cooldown_sec"])
        elif acc.fails >= 3:
            acc.cool_until = time.time() + int(CFG["cooldown_sec"])


# ── формат промпта из OpenAI messages ────────────────────────────────────

def build_prompt(messages, max_chars=24000):
    system = []
    turns = []
    for m in messages or []:
        role = (m.get("role") or "user").lower()
        content = m.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text") or "" for p in content
                              if isinstance(p, dict))
        content = (content or "").strip()
        if not content:
            continue
        if role == "system":
            system.append(content)
        elif role == "assistant":
            turns.append(("Assistant", content))
        else:
            turns.append(("User", content))
    if not turns:
        turns = [("User", "Привет! Ответь одним словом: ok")]
    head = ("Answer the last User message below. Use plain text, no tools, "
            "no preamble. Keep the answer focused.\n")
    if system:
        head += "System instructions: " + "\n".join(system) + "\n"
    body = "\n".join(f"{r}: {t}" for r, t in turns)
    prompt = head + "\n" + body
    if len(prompt) > max_chars:
        prompt = head + "\n…(earlier messages truncated)…\n" + body[-(max_chars - len(head) - 40):]
    return prompt


# ── запуск тура ──────────────────────────────────────────────────────────

def run_account(acc, prompt, timeout=None, model=None):
    timeout = timeout or int(CFG["timeout_sec"])
    cli = CFG["perch_cli"]
    if not os.path.exists(cli):
        return False, {"error": f"perch CLI не найден: {cli}"}, {}
    req_dir = os.path.join(DATA_DIR, "reqs", uuid.uuid4().hex)
    os.makedirs(req_dir, exist_ok=True)
    home = os.path.join(acc.dir, "home")
    os.makedirs(home, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "HOME": home,
        "PERCH_CLI_AUTH_DIR": os.path.join(acc.dir, "auth"),
        "PERCH_NO_UPDATE_NOTIFIER": "1",
    })
    cmd = [CFG["node_bin"], cli, "run", prompt, "--json", "--mode", "ask",
           "--cwd", req_dir]
    if model and model not in ("perch-auto", "auto"):
        cmd += ["--model", model]
    started = time.time()
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True,
                           timeout=timeout)
        out = (p.stdout or "").strip()
        try:
            data = json.loads(out)
        except ValueError:
            return False, {"error": f"cli exit {p.returncode}: "
                                    f"{(out or p.stderr)[-400:]}"}, {}
        ok = (p.returncode == 0 and data.get("ok")
              and data.get("status") == "completed")
        usage = ((data.get("contextSnapshot") or {}).get("turnApiUsage") or {})
        meta = {
            "model": ((data.get("contextSnapshot") or {}).get("modelId")
                      or data.get("model")),
            "duration_ms": int((time.time() - started) * 1000),
            "input_tokens": usage.get("inputTokens"),
            "output_tokens": usage.get("outputTokens"),
        }
        return bool(ok), data, meta
    except subprocess.TimeoutExpired:
        return False, {"error": f"timeout {timeout}s"}, {}
    except Exception as exc:                     # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}, {}
    finally:
        shutil.rmtree(req_dir, ignore_errors=True)


def log_usage(email, model, meta, ok, err=""):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "email": email, "model": model, "ok": ok,
           "duration_ms": meta.get("duration_ms"),
           "input_tokens": meta.get("input_tokens") or 0,
           "output_tokens": meta.get("output_tokens") or 0}
    if err:
        rec["error"] = err[:300]
    with _usage_lock:
        with open(CFG["usage_log"], "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── HTTP ─────────────────────────────────────────────────────────────────

def _json_response(h, code, obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _sse(h, obj):
    h.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode())
    h.wfile.flush()


def _openai_error(message, code="server_error", status=500):
    return {"error": {"message": message, "type": code, "code": code}}


class Handler(BaseHTTPRequestHandler):
    server_version = "perch2api/1.0"

    def log_message(self, *args):
        pass

    def _auth_ok(self):
        key = (CFG.get("api_key") or "").strip()
        if not key:
            return True
        got = (self.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        return got == key

    def do_GET(self):
        if self.path.startswith("/health"):
            reload_pool()
            return _json_response(self, 200, {
                "ok": True, "accounts": len(POOL),
                "usable": sum(1 for a in POOL if a.usable()),
                "inflight": sum(a.inflight for a in POOL),
                "cooldown": sum(1 for a in POOL if a.cool_until > time.time()),
            })
        if self.path.startswith("/v1/models"):
            if not self._auth_ok():
                return _json_response(self, 401, _openai_error("invalid api key", "invalid_api_key", 401))
            data = [{"id": m, "object": "model", "owned_by": "perch"}
                    for m in CFG["models"]]
            return _json_response(self, 200, {"object": "list", "data": data})
        return _json_response(self, 404, _openai_error("not found", "not_found", 404))

    def do_POST(self):
        if not self.path.startswith("/v1/chat/completions"):
            return _json_response(self, 404, _openai_error("not found", "not_found", 404))
        if not self._auth_ok():
            return _json_response(self, 401, _openai_error("invalid api key", "invalid_api_key", 401))
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return _json_response(self, 400, _openai_error("bad json", "invalid_request_error", 400))

        messages = req.get("messages") or []
        if not messages:
            return _json_response(self, 400, _openai_error(
                "messages is required", "invalid_request_error", 400))
        stream = bool(req.get("stream"))
        want_model = req.get("model") or "perch-auto"
        if want_model not in CFG["models"]:
            return _json_response(self, 400, _openai_error(
                f"unknown model {want_model}; see /v1/models",
                "invalid_request_error", 400))
        prompt = build_prompt(messages, int(CFG["max_prompt_chars"]))

        tried = set()
        last_err = "нет доступных аккаунтов"
        acc = None
        ok = False
        data = {}
        meta = {}
        for _ in range(2):
            reload_pool()
            acc = pick_account(exclude=tried)
            if not acc:
                break
            tried.add(acc.email)
            ok, data, meta = run_account(acc, prompt, model=want_model)
            release_account(acc, ok, data.get("error") or "")
            log_usage(acc.email, want_model, meta, ok, data.get("error") or "")
            if ok:
                break
            last_err = data.get("error") or str(data)[:300]
        if not ok:
            return _json_response(self, 503, _openai_error(
                f"Perch: {last_err}", "upstream_error", 503))

        text = (data.get("assistantText") or "").strip()
        usage = {"prompt_tokens": meta.get("input_tokens") or 0,
                 "completion_tokens": meta.get("output_tokens") or 0,
                 "total_tokens": (meta.get("input_tokens") or 0)
                                  + (meta.get("output_tokens") or 0)}
        model_out = meta.get("model") or want_model
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())

        if not stream:
            return _json_response(self, 200, {
                "id": cid, "object": "chat.completion", "created": created,
                "model": model_out,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}],
                "usage": usage,
                "perch": {"account": acc.email, "duration_ms": meta.get("duration_ms")},
            })

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        base = {"id": cid, "object": "chat.completion.chunk", "created": created,
                "model": model_out}
        _sse(self, {**base, "choices": [{"index": 0, "delta": {"role": "assistant"},
                                         "finish_reason": None}]})
        for i in range(0, len(text), 160):
            _sse(self, {**base, "choices": [{"index": 0,
                                             "delta": {"content": text[i:i + 160]},
                                             "finish_reason": None}]})
        _sse(self, {**base, "choices": [{"index": 0, "delta": {},
                                         "finish_reason": "stop"}],
                    "usage": usage})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main():
    reload_pool(force=True)
    srv = ThreadingHTTPServer((CFG["host"], int(CFG["port"])), Handler)
    print(f"== perch2api :{CFG['port']} · аккаунтов {len(POOL)} "
          f"(живых {sum(1 for a in POOL if a.usable())}) ==", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
