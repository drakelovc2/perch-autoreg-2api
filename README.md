# perch-autoreg-2api

Автореггер [Perch](https://chat.perchai.app) + OpenAI-совместимый мост к нему —
одним репозиторием. Регистрация идёт через **GitHub OAuth** и официальный
`perchai-cli`, поэтому не нужны ни почта, ни капча-сервисы. Мост превращает
CLI-сессию аккаунта в привычный `/v1/chat/completions`.

**Зачем:** бесплатный план Perch **Starter** — $0 без карты, **$20 (20 000 PT)
включённого использования в месяц**, 35 именованных моделей: Claude Opus/Sonnet/
Haiku 5.5, GPT-6.1 Sol/Terra, GPT-6 Sol/Luna, Gemini 4 Argon, Kimi K3/K3.1 Pro,
Grok 4.6/4.7, DeepSeek V4/V4.1, Qwen 3.8 Max/Flash/27B, GLM 5.2/5.3,
MiniMax M3, Nemotron Ultra/Lightning, Beam 1.0, Crouton Alpha, Hy4, Inkling.

```
perch-autoreg-2api/
├── register/   автореггер: GitHub OAuth → CLI-сессия → выбор Starter
└── bridge/     perch2api: OpenAI /v1/chat/completions поверх CLI-сессий пула
```

## Как это работает

1. `register/` запускает `perch login github` (официальный CLI) и подменяет
   `xdg-open`, чтобы поймать Supabase OAuth URL. Camoufox проходит GitHub
   login + TOTP 2FA + «Authorize», CLI сам обменивает PKCE-код и сохраняет
   сессию (`cli-auth-session.json`).
2. Реггер выбирает бесплатный план Starter через
   `POST /api/perch-terminal/cli-plan {plan:"starter"}` и (опционально)
   проверяет аккаунт живым туром `perch run`.
3. `bridge/` на каждый запрос выбирает аккаунт из пула и запускает
   `perch run "<prompt>" --json --mode ask` с `PERCH_CLI_AUTH_DIR` этого
   аккаунта. Свежий cwd на запрос = свежий тред (история не утекает).
   Ответ собирается из `assistantText`, usage — из `turnApiUsage`.

API-ключей у Perch нет: авторизация — CLI-сессия (Supabase JWT + refresh),
CLI обновляет токен сам.

## Быстрый старт

Нужны: **Node ≥ 22**, **Python ≥ 3.10**, `npm`, `git`.

```bash
git clone https://github.com/<you>/perch-autoreg-2api.git
cd perch-autoreg-2api/register

# 1) зависимости реггера (camoufox = свой Firefox, скачается при первом запуске)
python3 -m venv .venv
.venv/bin/pip install requests playwright camoufox pyotp
.venv/bin/python -m camoufox fetch

# 2) официальный CLI Perch в .cli/
bash tools/install-cli.sh

# 3) пул GitHub-аккаунтов и конфиг
cp config.example.json config.json
#    gh_pool_file: строки вида
#    email:password:TOTP_SECRET:recovery_codes:username
#    (username обязателен; TOTP — base32)
.venv/bin/python main.py pool        # покажет занятые/свободные

# 4) регистрация
.venv/bin/python main.py register    # один аккаунт
.venv/bin/python main.py batch 5     # пачка
.venv/bin/python main.py accounts

# 5) панель (опционально): http://127.0.0.1:9943
bash start.sh
```

Мост:

```bash
cd ../bridge
cp config.example.json config.json
bash start.sh                        # http://127.0.0.1:12066
curl -s http://127.0.0.1:12066/health
curl -s http://127.0.0.1:12066/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-5.5","messages":[{"role":"user","content":"Привет"}]}'
```

## Модели

`GET /v1/models` отдаёт 35 именованных моделей + `perch-auto` (по умолчанию
роутер выбирает `Qwen/Qwen3.6-35B-A3B`). На Starter доступны все, включая
Claude Opus 5.5 и GPT-6.1 Sol — расход идёт с включённых $20/20 000 PT.
`model` в запросе передаётся CLI как `--model`; неизвестная модель → 400.

## Ограничения

- **Латентность 15–40 с** на запрос: Node-старт + агентный тур Perch. Это
  батч/фоновый сценарий, не интерактивный чат.
- Один тур — **~1.3–8k входных токенов** (системный контекст Perch). Оценка
  ёмкости: дешёвые модели ~$0.002/тур → ~10 000 туров на аккаунт; Claude Opus
  5.5 ~$0.03/тур → ~600 туров. Лимит месячный, не переносится.
- `stream=true` — псевдо-стрим: чанки отдаются после завершения тура.
- Точный PT-счётчик Perch закрыт (RPC только для service_role); расход
  считается по `estimated_cost_usd` из `perch_ai_inference_usage`.

## Прокси (опционально)

По умолчанию регистрация идёт с вашего IP — GitHub OAuth и Perch это проходят.
Если нужен прокси с авторизацией (например, Bright Data), браузер не умеет
логин/пароль в `--proxy-server`, поэтому в `register/tools/` есть
`relay_service.py` (локальный релей, добавляющий `Proxy-Authorization`) и
`vps-tunnel.sh` (проброс порта на VPS, если реггер живёт там, а прокси
привязан к IP домашней машины).

## Дисклеймер

Код предоставляется «как есть», для личных и исследовательских задач.
Соблюдайте Terms of Service Perch и GitHub: массовая регистрация аккаунтов
может нарушать правила сервисов. Автор не несёт ответственности за блокировки
и последствия использования.
