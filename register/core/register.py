# -*- coding: utf-8 -*-
"""Perch (perchai.app) — авторег через GitHub OAuth + официальный perchai-cli.

Живая разведка 07.11.2026.

Флоу (проверен живьём на аккаунте пула):
  1. `perch login github` — официальный CLI (npm `perchai-cli`) поднимает
     локальный callback `http://127.0.0.1:<port>/callback` и открывает
     Supabase OAuth URL через xdg-open. Мы подменяем xdg-open своим
     скриптом и ловим URL (сам CLI при этом продолжает ждать callback).
  2. camoufox открывает URL → GitHub login (пул `gitaccs.txt`,
     email:password:TOTP:recovery:username) → TOTP 2FA →
     «Authorize Perch-Terminal-AI».
     ВАЖНО: у GitHub ОБЕ кнопки (Cancel и Authorize) имеют
     `name="authorize"` — кликаем по тексту `^Authorize`, иначе Cancel.
  3. GitHub → Supabase → `127.0.0.1:<port>/callback?code=…`; CLI меняет
     PKCE-код на сессию и пишет `cli-auth-session.json`
     (version/appUrl/accessToken/refreshToken/expiresAt/userId/email).
  4. Бесплатный план Starter: `GET {appUrl}/api/perch-terminal/cli-plan`
     → noticeVersion; `POST … {plan:"starter", noticeVersion}`.
     Starter = $0 без карты, 20 000 PT включённого использования в месяц
     (Laguna S 2.1, DeepSeek V4.1 Flash, GLM 5.3 Flash, Qwen 3.8 Max).
  5. (опц.) живая проверка: `perch run "…" --json --mode ask` в чистом cwd.
     Один прогон ~6.4k входных токенов (системный контекст Perch).

Выхлоп: аккаунт с готовой CLI-сессией; мост `perch2api` запускает
`perch run` с `PERCH_CLI_AUTH_DIR` этого аккаунта.

Контракт шаблона: register_one(), test_key(), list_models(), free_models(),
ServiceClient (имя сохранено — его импортирует core/limits.py).
"""
import json
import os
import re
import secrets
import shutil
import string
import subprocess
import time

import requests

from . import config
from . import engines

# ── константы флоу ────────────────────────────────────────────────────────
WEB_URL = "https://chat.perchai.app"
APP_URL_DEFAULT = "https://app.perchai.app"
CLI_PLAN_PATH = "/api/perch-terminal/cli-plan"

SEL_EMAIL = "#login_field"
SEL_PASSWORD = "#password"
SEL_2FA = '#app_totp, input[name="app_otp"], input[autocomplete="one-time-code"]'


class ServiceError(Exception):
    pass


def _step(msg, on_step=None):
    if on_step:
        on_step(msg)
    else:
        print(f"  {msg}", flush=True)


# ── GitHub-пул (тот же формат, что у piapi/wavespeed/cometapi) ─────────────

def load_pool(path=None, limit=0):
    """gitaccs.txt: email:password:TOTP_SECRET:recovery_codes:username."""
    path = path or config.get("gh_pool_file")
    out = []
    if not path or not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) != 5:
                continue
            email, password, totp, recovery, username = parts
            if not (username and totp and password):
                continue
            out.append({"email": email, "password": password,
                        "totp": totp.strip().upper().replace(" ", ""),
                        "recovery": recovery, "username": username,
                        "pool_index": i})
    return out[:limit] if limit else out


def used_usernames():
    from . import results
    return {a.get("github_username") for a in results.load_accounts()
            if a.get("github_username")}


# ── CLI/npm окружение ─────────────────────────────────────────────────────

def _node_bin():
    return config.get("node_bin") or "node"


def _perch_cli():
    p = config.get("perch_cli")
    if p and os.path.exists(p):
        return p
    local = os.path.join(config.ROOT, ".cli", "node_modules", ".bin", "perch")
    if os.path.exists(local):
        return local
    return shutil.which("perch")


def ensure_cli(on_step=None):
    """Установить perchai-cli в .cli/ (npm), если ещё не установлен."""
    if _perch_cli():
        return _perch_cli()
    d = os.path.join(config.ROOT, ".cli")
    os.makedirs(d, exist_ok=True)
    _step("ставлю perchai-cli в .cli/ (npm)…", on_step)
    npm = config.get("npm_bin") or "npm"
    subprocess.run([npm, "install", "--prefix", d, "--no-audit", "--no-fund",
                    "perchai-cli@" + str(config.get("perch_cli_version") or "latest")],
                   check=True, timeout=600,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    cli = _perch_cli()
    if not cli:
        raise ServiceError("perchai-cli не установился")
    return cli


def _account_dir(email, tag=None):
    safe = "".join(c if c.isalnum() or c in "-_.@" else "_" for c in email)[:80]
    return os.path.join(config.DATA_DIR, "accounts", (tag + "_" if tag else "") + safe)


def _session_file(acc_dir):
    return os.path.join(acc_dir, "auth", "cli-auth-session.json")


def _cli_env(acc_dir, extra_path=None):
    env = dict(os.environ)
    home = os.path.join(acc_dir, "home")
    os.makedirs(home, exist_ok=True)
    env.update({
        "HOME": home,
        "PERCH_CLI_AUTH_DIR": os.path.join(acc_dir, "auth"),
        "PERCH_NO_UPDATE_NOTIFIER": "1",
    })
    if extra_path:
        env["PATH"] = extra_path + ":" + env.get("PATH", "")
    return env


# ── план и usage через HTTP (Bearer = accessToken сессии) ────────────────

def _api(acc, method, path, body=None, timeout=30):
    url = (acc.get("app_url") or APP_URL_DEFAULT).rstrip("/") + path
    headers = {"Accept": "application/json",
               "Authorization": "Bearer " + (acc.get("access_token") or "")}
    if body is not None:
        headers["Content-Type"] = "application/json"
    r = requests.request(method, url, headers=headers,
                         data=json.dumps(body) if body is not None else None,
                         timeout=timeout)
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:500]}
    return r.status_code, data


def plan_status(acc):
    st, d = _api(acc, "GET", CLI_PLAN_PATH)
    return st, d


def choose_starter(acc):
    st, d = plan_status(acc)
    if st != 200:
        raise ServiceError(f"cli-plan GET {st}: {d}")
    if not d.get("planRequired"):
        return {"ok": True, "already": True}
    nv = ((d.get("starter") or {}).get("noticeVersion")) or ""
    st2, d2 = _api(acc, "POST", CLI_PLAN_PATH,
                   {"plan": "starter", "noticeVersion": nv})
    if st2 != 200 or not d2.get("ok"):
        raise ServiceError(f"starter POST {st2}: {d2}")
    return {"ok": True, "notice": nv, "resp": d2}


# ── запуск CLI-тура (используется и мониторингом, и мостом) ──────────────

def run_prompt(acc, prompt, cwd=None, mode="ask", timeout=300, thread=None):
    """`perch run` в окружении аккаунта → (ok, dict).

    cwd — рабочий каталог (threadId у Perch выводится из cwd: свежий cwd =
    свежий тред, поэтому мост даёт каждому запросу свой каталог).
    """
    cli = _perch_cli()
    if not cli:
        raise ServiceError("perchai-cli не найден (bash tools/install-cli.sh)")
    acc_dir = acc.get("account_dir") or _account_dir(acc.get("email") or "anon")
    cwd = cwd or os.path.join(acc_dir, "run")
    os.makedirs(cwd, exist_ok=True)
    cmd = [_node_bin(), cli, "run", prompt, "--json", "--mode", mode, "--cwd", cwd]
    if thread:
        cmd += ["--thread", thread]
    env = _cli_env(acc_dir)
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, {"error": f"timeout {timeout}s"}
    out = (p.stdout or "").strip()
    try:
        data = json.loads(out)
    except ValueError:
        return False, {"error": f"cli exit {p.returncode}: {(out or p.stderr)[-400:]}"}
    ok = p.returncode == 0 and data.get("ok") and data.get("status") == "completed"
    if not ok:
        data.setdefault("error", (p.stderr or "")[-300:])
    return bool(ok), data


# ── браузерный логин ─────────────────────────────────────────────────────

class ServiceClient:
    """Один camoufox-профиль на GitHub-аккаунт и один CLI-процесс login."""

    def __init__(self, proxy=None, on_step=None):
        self.proxy = proxy
        self.on_step = on_step
        self.profile = None
        self.br = None
        self.page = None
        self.proc = None
        self.proc_log = None

    def _log(self, msg):
        _step(msg, self.on_step)

    def open(self, email):
        self.profile = engines.make_profile(
            os.path.join(config.DATA_DIR, "profiles"),
            account="perch-" + (email or "anon"), engine="camoufox")
        eng = engines.resolve("camoufox", stealth=True)
        self.br = engines.launch(eng, self.profile, proxy=self.proxy, headless=True)
        self.page = self.br.new_page()
        self.page.set_default_timeout(60000)
        return self

    def close(self):
        if self.br:
            try:
                self.br.close()
            except Exception:
                pass
            self.br = None
            self.page = None
        self._stop_cli()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _stop_cli(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        self.proc = None

    # ── шаг 1: CLI login + ловля OAuth URL ────────────────────────────────

    def start_cli_login(self, acc_dir):
        cli = ensure_cli(self.on_step)
        os.makedirs(os.path.join(acc_dir, "bin"), exist_ok=True)
        os.makedirs(os.path.join(acc_dir, "auth"), exist_ok=True)
        url_file = os.path.join(acc_dir, "oauth_url.txt")
        if os.path.exists(url_file):
            os.unlink(url_file)
        xdg = os.path.join(acc_dir, "bin", "xdg-open")
        with open(xdg, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/bash\nprintf "%s" "$1" > "{url_file}"\nexit 0\n')
        os.chmod(xdg, 0o755)

        env = _cli_env(acc_dir, extra_path=os.path.join(acc_dir, "bin"))
        log_path = os.path.join(acc_dir, "cli-login.log")
        self.proc_log = open(log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [_node_bin(), cli, "login", "github"], env=env,
            stdout=self.proc_log, stderr=subprocess.STDOUT)
        self._log("step 1/5: perch login github запущен, жду OAuth URL…")
        for _ in range(80):
            if os.path.exists(url_file):
                url = open(url_file, encoding="utf-8").read().strip()
                if url:
                    return url
            if self.proc.poll() is not None:
                raise ServiceError(
                    f"CLI завершился до callback: {self._cli_log(acc_dir)[-300:]}")
            time.sleep(0.5)
        raise ServiceError("OAuth URL не появился за 40 с")

    def _cli_log(self, acc_dir):
        try:
            return open(os.path.join(acc_dir, "cli-login.log"),
                        encoding="utf-8").read()
        except OSError:
            return ""

    # ── шаг 2-3: GitHub login + TOTP + authorize ─────────────────────────

    def github_oauth(self, url, github):
        import pyotp
        self._log("step 2/5: открываю OAuth URL (GitHub)…")
        self.page.goto(url, wait_until="domcontentloaded", timeout=90000)
        self.page.wait_for_timeout(4000)

        if self.page.locator(SEL_EMAIL).count():
            self.page.fill(SEL_EMAIL, github["username"])
            self.page.fill(SEL_PASSWORD, github["password"])
            self.page.wait_for_timeout(300)
            self.page.click('input[type="submit"][name="commit"], '
                            'button[type="submit"]')
            self._log("step 2/5: логин/пароль GitHub отправлены")
            self.page.wait_for_timeout(5000)

        # TOTP (до 3 окон подряд). ВАЖНО: кликаем ТОЛЬКО внутри формы
        # two-factor — generic button[type=submit] на странице согласия
        # попадает в «Cancel» (у обеих кнопок name="authorize").
        for attempt in range(3):
            if not self.page.locator(SEL_2FA).count():
                break
            code = pyotp.TOTP(github["totp"]).now()
            self._log(f"step 3/5: 2FA (попытка {attempt + 1})")
            self.page.fill(SEL_2FA, code)
            self.page.wait_for_timeout(300)
            try:
                self.page.keyboard.press("Enter")
            except Exception:
                pass
            self.page.wait_for_timeout(1200)
            if self.page.locator(SEL_2FA).count():
                clicked = False
                for sel in ('form[action*="two-factor"] button[type="submit"]',
                            'button:has-text("Verify")',
                            'button:has-text("Continue")'):
                    try:
                        self.page.click(sel, timeout=4000)
                        clicked = True
                        self._log(f"step 3/5: 2FA submit: {sel}")
                        break
                    except Exception:
                        continue
                if not clicked:
                    self._log("step 3/5: кнопка 2FA не найдена (Enter уже отправлен?)")
            # ждём, пока 2FA-поле исчезнет (переход на consent)
            for _ in range(20):
                if not self.page.locator(SEL_2FA).count():
                    break
                self.page.wait_for_timeout(500)
            if not self.page.locator(SEL_2FA).count():
                break
            time.sleep(31)

        # OAuth consent: кнопка Authorize — только по тексту ^Authorize
        self._log("step 3/5: жду страницу согласия…")
        clicked = False
        for _ in range(20):
            try:
                btn = self.page.get_by_role(
                    "button", name=re.compile(r"^Authorize", re.I)).last
                if btn.count():
                    btn.click(timeout=8000)
                    clicked = True
                    break
            except Exception:
                pass
            self.page.wait_for_timeout(1000)
        if clicked:
            self._log("step 3/5: Authorize нажат")
            self.page.wait_for_timeout(7000)
        else:
            low = self._text().lower()
            if "authorize" in low:
                raise ServiceError("страница согласия есть, но Authorize не нажался")
            self._log("step 3/5: страница согласия не показана (уже авторизовано)")
        return True

    def _text(self):
        try:
            return self.page.inner_text("body")[:1500]
        except Exception:
            return ""

    # ── шаг 4: ждём сессию от CLI ────────────────────────────────────────

    def wait_session(self, acc_dir, timeout=90):
        sf = _session_file(acc_dir)
        self._log("step 4/5: жду cli-auth-session.json…")
        for _ in range(timeout):
            if os.path.exists(sf):
                try:
                    sess = json.load(open(sf, encoding="utf-8"))
                except ValueError:
                    sess = None
                if sess and sess.get("accessToken"):
                    return sess
            if self.proc and self.proc.poll() is not None:
                break
            time.sleep(1)
        raise ServiceError(f"сессия не появилась: {self._cli_log(acc_dir)[-400:]}")


# ── полный флоу ───────────────────────────────────────────────────────────

def _gen_password(n=18):
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(n))


def register_one(github=None, proxy=None, mail=None, on_step=None):
    """Регистрация одного Perch-аккаунта через GitHub OAuth.

    github — dict из load_pool(); если None, берём первый неиспользованный.
    """
    used = used_usernames()
    if github is None:
        pool = [a for a in load_pool() if a["username"] not in used]
        if not pool:
            return {"error": "пул GitHub исчерпан (все username уже в accounts.jsonl)"}
        github = pool[0]
    elif github["username"] in used:
        return {"error": f"github {github['username']} уже использован"}
    email = github["email"]
    acc_dir = _account_dir(email)
    os.makedirs(acc_dir, exist_ok=True)
    client = ServiceClient(proxy=proxy, on_step=on_step)
    try:
        client.open(email)
        client._log(f"step 1/5: Perch signup через GitHub {github['username']}")
        url = client.start_cli_login(acc_dir)
        client.github_oauth(url, github)
        sess = client.wait_session(acc_dir)

        acc = {
            "email": email,
            "github_username": github["username"],
            "user_id": sess.get("userId"),
            "app_url": sess.get("appUrl") or APP_URL_DEFAULT,
            "access_token": sess.get("accessToken"),
            "refresh_token": sess.get("refreshToken"),
            "expires_at": sess.get("expiresAt"),
            "account_dir": acc_dir,
            "session_file": _session_file(acc_dir),
            "plan": "starter",
            "engine": "camoufox+cli",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        client._log("step 5/5: выбираю бесплатный Starter…")
        try:
            choose_starter(acc)
        except ServiceError as exc:
            acc["error"] = f"сессия есть, но Starter не выбран: {exc}"
            return acc
        st, d = plan_status(acc)
        acc["plan_required"] = d.get("planRequired")

        if config.get("liveness_run", True):
            client._log("step 5/5: живая проверка perch run…")
            ok, res = False, {}
            # «The model is busy» бывает транзиентным — до 3 попыток.
            for attempt in range(3):
                ok, res = run_prompt(
                    acc, "Reply with exactly: ok",
                    timeout=int(config.get("run_timeout_sec") or 300))
                if ok:
                    break
                err = str(res.get("error") or res)
                client._log(f"step 5/5: тур не прошёл ({attempt + 1}/3): "
                            f"{err[:120]}")
                if "busy" not in err.lower() and "quota" not in err.lower():
                    break
                time.sleep(20)
            usage = ((res.get("contextSnapshot") or {}).get("turnApiUsage") or {})
            acc["live_ok"] = bool(ok) if ok else None
            acc["live_answer"] = (res.get("assistantText") or "")[:120]
            acc["live_model"] = ((res.get("contextSnapshot") or {}).get("modelId"))
            acc["input_tokens"] = usage.get("inputTokens")
            acc["output_tokens"] = usage.get("outputTokens")
            if not ok:
                # Сессия и план валидны; тур мог не пройти из-за занятости
                # модели. Аккаунт НЕ хороним — live_ok=None, ошибка в live_error.
                acc["live_error"] = str(res.get("error") or res)[:300]
        else:
            acc["live_ok"] = None
        return acc
    except ServiceError as exc:
        return {"email": email, "github_username": github["username"],
                "account_dir": acc_dir, "error": str(exc)}
    except Exception as exc:
        return {"email": email, "github_username": github["username"],
                "account_dir": acc_dir,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    finally:
        client.close()


# ── проверка/служебное ───────────────────────────────────────────────────

def refresh_session(acc):
    """Сессия CLI обновляет токен сама при запуске; здесь просто читаем файл."""
    sf = acc.get("session_file") or _session_file(acc.get("account_dir") or "")
    try:
        return json.load(open(sf, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check_account(acc, proxy=None, on_step=None):
    """Проверка аккаунта: план (HTTP) + опционально короткий run."""
    try:
        st, d = plan_status(acc)
        if st != 200:
            return {"ok": False, "error": f"cli-plan HTTP {st}: {d}"}
        out = {"ok": True, "plan_required": d.get("planRequired")}
        if config.get("check_with_run", False):
            ok, res = run_prompt(acc, "Reply with exactly: ok",
                                 timeout=int(config.get("run_timeout_sec") or 300))
            out["run_ok"] = ok
            out["answer"] = (res.get("assistantText") or "")[:120]
            if not ok:
                out["ok"] = False
                out["error"] = res.get("error") or str(res)[:200]
        return out
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def list_models(api_key=None, proxy=None):
    """Именованные модели, которые CLI принимает в `--model` (живой список
    из ошибки Unknown model, проверен 07.11.2026). На Starter доступны все —
    списываются с включённых $20/20 000 PT."""
    return ["deepseek-flash-starter", "glm-5.3-flash-starter", "crouton-alpha", "gemini-4-argon", "claude-opus-5.5", "gpt-6.1-sol", "gpt-6.1-terra", "kimi-3.1-pro", "hy4", "deepseek-v4.1-flash-1017", "gpt-6-luna", "claude-sonnet-5.5", "claude-haiku-5.5", "kimi-3", "grok-4.7", "grok-4.6", "glm-5.3", "glm-5.3-flash", "qwen-3.8-flash", "qwen-3.8-max", "qwen-3.8", "minimax-m3", "nemotron-ultra", "nemotron-lightning", "deepseek-v4", "deepseek-v4-flash-0731-262k", "qwen-3.7-plus", "inkling", "beam-1.0", "kimi-2.7", "glm-5.2", "gpt-6-sol", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]


def free_models(api_key=None, proxy=None):
    """На Starter платных Perch Tokens нет — $20 включённого использования."""
    return list_models()


def test_key(api_key=None, model=None, proxy=None):
    """Perch не выдаёт API-ключей: мост работает по CLI-сессии аккаунта."""
    return False, model, ("Perch: API-ключей нет; мост запускает perch CLI "
                          "с сессией аккаунта (Starter free)")
