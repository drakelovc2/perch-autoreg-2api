# PROTOCOL — живой протокол Perch (CLI + GitHub OAuth)

Снято 07.11.2026: разбор npm-пакета `perchai-cli@2.4.125`, живые прогоны
`perch login github`, GitHub OAuth в camoufox, выбор плана и `perch run`.

Сервис: **chat.perchai.app** (приложение), **app.perchai.app** (appUrl в
сессии), бэкенд — Supabase (`zlfuvsfjtgsdtqcaykia.supabase.co`).
CLI: `npm i -g perchai-cli`, бинарь `perch`, node >= 22.

---

## 1. Установка и сессия

```bash
perch login github        # браузерный OAuth, сессия сохраняется
perch status              # «Signed in as …»
```

Сессия: `~/.perch/cli-auth-session.json` (можно переопределить
`PERCH_CLI_AUTH_DIR`), формат:

```json
{"version":1,"appUrl":"https://app.perchai.app",
 "accessToken":"<Supabase JWT ES256>","refreshToken":"…",
 "expiresAt":1794072694,"userId":"<uuid>…",
 "email":"…@example.com","updatedAt":"2026-11-07T16:31:34.446Z"}
```

CLI сам обновляет accessToken по refreshToken при запуске.

## 2. OAuth-флоу (из бандла `dist/perch.mjs`)

```
GET  {appUrl}/api/perch-terminal/cli-auth/config
     → {ok, supabaseUrl, supabaseAnonKey, providers:[…], redirectHost}
     (providers по умолчанию: google, github; CLI берёт google, если есть,
      иначе github — поэтому в реггере явно `perch login github`)

CLI поднимает локальный HTTP-сервер на 127.0.0.1:<port> и открывает
xdg-open'ом (linux) URL:

POST {supabaseUrl}/auth/v1/authorize?provider=github
     &redirect_to=http%3A%2F%2F127.0.0.1%3A<port>%2Fcallback
     &code_challenge=…&code_challenge_method=s256

GitHub login → Authorize Perch-Terminal-AI
→ https://<supabase>/auth/v1/callback?code=… → 127.0.0.1:<port>/callback?code=…
CLI: exchangeCodeForSession → cli-auth-session.json
```

Ловля URL: подменяем `xdg-open` скриптом, который пишет `$1` в файл.
CLI при этом продолжает ждать callback — его нельзя убивать.

### Ловушка GitHub

На странице согласия **две** кнопки с `name="authorize"`:
`Cancel` и `Authorize Perch-Terminal-AI`. Клик по `button[name="authorize"]`
или generic `button[type=submit]` попадает в **Cancel** → CLI получает
`error=access_denied` («The user has denied your application access»).
Кликать только по тексту: `get_by_role("button", name=re.compile("^Authorize"))`.

## 3. План (Starter, free)

```
GET  {appUrl}/api/perch-terminal/cli-plan        Authorization: Bearer <accessToken>
 → {"ok":true,"planRequired":true,
    "starter":{"noticeVersion":"2026-09-13","notice":"…","privacyUrl":"…","termsUrl":"…"},
    "proUrl":"…"}

POST {appUrl}/api/perch-terminal/cli-plan
     {"plan":"starter","noticeVersion":"2026-09-13"}
 → {"ok":true,"planRequired":false}
```

Тарифы (perchai.app/pricing, 07.11.2026):

| План | Цена | Использование | Модели |
|---|---|---|---|
| **Starter** | **$0, без карты** | **$20 / 20 000 PT в месяц** | Laguna S 2.1, DeepSeek V4.1 Flash, GLM 5.3 Flash, Qwen 3.8 Max |
| Pro | $10/мес (early access) | до $150/мес, лимиты 5ч/7д | + Gemini 4 Argon, Claude Opus 5.5, GPT-6.1 Sol и ещё 29 |
| Ultra | $30/мес | до $450/мес, без 5-часового лимита | + Claude Fable 5.1, GPT Astra |

Без выбранного плана тур отвечает `planChoice` («This account needs a plan…»).

## 4. Тур: `perch run`

```bash
perch run "Reply with exactly: ok" --json --mode ask --cwd <dir>
```

Флаги: `--thread <id>`, `--model standard|standard 1m|pro|pro 1m`,
`--effort off|low|…|ultra`, `--attach`, `--skill`.
`--mode ask` — без агентных инструментов (чистый ответ), `agents`/`plan` —
агентные режимы.

Ответ (`--json`):

```json
{"ok":true,"status":"completed","prompt":"…","cwd":"…",
 "threadId":"cli-cwd-<hash>","runId":"tkt-cli-…","durationMs":16164,
 "assistantText":"ok",
 "events":[{"type":"turn_started"},{"type":"context_summary"},
           {"type":"model_call_started"},{"type":"model_call_failed",
            "error":"This model's quota is used up for now…"},
           {"type":"model_call_completed","durationMs":14501,"outputTokens":2},
           {"type":"turn_completed"}],
 "contextSnapshot":{"modelId":"Qwen/Qwen3.6-35B-A3B","provider":"wandb",
                    "turnApiUsage":{"inputTokens":6362,"outputTokens":2}}}
```

Важно: `threadId` выводится из `cwd` (`cli-cwd-<hash>`) — свежий cwd даёт
новый тред. Поэтому мост `perch2api` запускает каждый запрос в своём
временном каталоге, чтобы история не утекала между запросами.

Стоимость одного короткого тура — **~6.4–8k входных токенов** (системный
контекст Perch: PERCH.md, memory, скиллы). Латентность живьём 28–30 с
(включая Node-старт ~1 с и retry на «квота модели исчерпана»).

## 5. Внутренние эндпоинты (для будущего быстрого моста)

В бандле CLI есть прямая модель-прокси-схема (без агентного рантайма):

```
POST {appUrl}/api/perch-terminal/turn-ticket   → {ticket,ticketId,runId,expiresAt}
POST {appUrl}/api/perch-terminal/model-call
     Authorization: Bearer <accessToken>
     x-perch-turn-ticket: <ticket>
     body: {runId, lane:"chat", messages:[…], tools:[…], …}
POST {appUrl}/api/perch-terminal/cli-context   → session/workspace, память
```

`model-call` стримит ответ и умеет дешёвый чат без агентного контекста —
кандидат на v2 моста (ниже латентность и расход). Пока v1 моста запускает
`perch run` (надёжно, но 15–40 с и ~8k токенов на запрос).

## 6. Живые результаты (07.11.2026)

- OAuth через GitHub: логин, TOTP 2FA и Authorize проходят в camoufox.
- План Starter выбирается API'ом (`planRequired: false`), пробный тур
  `perch run` отвечает.
- Один провайдерский кейс: если на странице согласия GitHub кликнуть
  generic-`submit`, попадаешь в **Cancel** (обе кнопки `name="authorize"`) —
  CLI получает `access_denied`. Кликать только по тексту `^Authorize`.
- Батч на 32 аккаунта: все `live_ok=true`, фейлов нет (аккаунты — на VPS,
  браузер и Node работают там; прокси-релей — дома, если прокси нужен).
