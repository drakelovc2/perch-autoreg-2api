# register — автореггер Perch (GitHub OAuth + perchai-cli)

Регистрирует аккаунты [Perch](https://chat.perchai.app) через GitHub OAuth,
выбирает бесплатный **Starter** и сохраняет готовую CLI-сессию. Пул аккаунтов
потребляет [`../bridge`](../bridge) (OpenAI-совместимый шлюз).

## Требования

- Node ≥ 22, `npm` (ставит `perchai-cli` в `.cli/`)
- Python ≥ 3.10, пакеты `requests playwright camoufox pyotp`
- camoufox-браузер: `python -m camoufox fetch`

```bash
python3 -m venv .venv
.venv/bin/pip install requests playwright camoufox pyotp
.venv/bin/python -m camoufox fetch
bash tools/install-cli.sh
cp config.example.json config.json
```

## Пул GitHub-аккаунтов

`config.json → gh_pool_file` (по умолчанию `~/gitaccs.txt`), по строке:

```
email:password:TOTP_SECRET:recovery_codes:username
```

`username` обязателен, `TOTP_SECRET` — base32 (2FA). Дедуп по
`github_username` в `data/accounts.jsonl`; `main.py pool` показывает пул без
секретов.

## Запуск

```bash
.venv/bin/python main.py register            # первый свободный аккаунт пула
.venv/bin/python main.py register --github <username>
.venv/bin/python main.py batch 5             # пачка, circuit breaker 3 фейла
.venv/bin/python main.py accounts
.venv/bin/python main.py check <email|github>
.venv/bin/python main.py models
bash start.sh                                # панель :9943
```

Опции в `config.json`: `liveness_run` (проверочный тур после регистрации,
по умолчанию `true`), `run_timeout_sec`, `batch_interval_sec`,
`circuit_breaker_fails`.

## Флоу

1. `perch login github` поднимает localhost-callback и открывает Supabase
   OAuth URL через `xdg-open`. Реггер подменяет `xdg-open` своим скриптом и
   ловит URL — CLI продолжает ждать callback.
2. camoufox: GitHub login (`#login_field`, `#password`) → TOTP 2FA →
   **Authorize**. У GitHub обе кнопки (Cancel/Authorize) имеют
   `name="authorize"` — клик только по тексту `^Authorize`.
3. CLI обменивает PKCE-код и пишет `cli-auth-session.json`
   (`appUrl/accessToken/refreshToken/expiresAt/userId/email`).
4. `POST /api/perch-terminal/cli-plan {plan:"starter", noticeVersion}` —
   бесплатный план выбран.
5. (опц.) `perch run "Reply with exactly: ok" --json --mode ask` — живая
   проверка; транзиентный «model is busy» ретраится до 3 раз.

Данные аккаунта: `data/accounts/<email>/` — `auth/cli-auth-session.json`,
изолированный `home/`, лог `cli-login.log`.

Подробный протокол (ответы Clerk-подобных API, эндпоинты, формат `perch run
--json`) — [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Панель (:9943)

Кнопка регистрации, прогресс по воркерам, стоп, список аккаунтов, лимиты,
модели. Слушает только `127.0.0.1`.

## VPS (опционально)

Тяжёлая часть — camoufox + Node на каждый прогон. Можно вынести реггер на VPS:

```bash
# на VPS: код, .venv, .cli, gitaccs.txt, config.json (свои пути)
VPS=user@host bash tools/vps-tunnel.sh start   # релей+туннель (если прокси привязан к дому)
```

`tools/sync-from-vps.sh` стягивает `accounts.jsonl` и сессии на локальную
машину (пути переписываются) — удобно, если мост остаётся локальным.

## Проверка

```bash
.venv/bin/python tests/selfcheck.py     # exit 0 = pass, сеть не нужна
```

## Дисклеймер

Только для личных/исследовательских задач. Массовая регистрация может
нарушать ToS Perch/GitHub — используйте на свой риск.
