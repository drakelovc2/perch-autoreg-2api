# bridge — perch2api (OpenAI-совместимый шлюз)

Превращает CLI-сессии Perch из [`../register`](../register) в обычный
OpenAI API: `/v1/chat/completions` + `/v1/models` + `/health`. Один запрос =
один тур официального `perch run --json --mode ask` в свежем рабочем каталоге.

## Запуск

```bash
cp config.example.json config.json
bash start.sh                 # http://127.0.0.1:12066
curl -s http://127.0.0.1:12066/health
```

Нужны: Node ≥ 22 и установленный реггер (`.cli` + `data/accounts.jsonl`).
Если реггер лежит в другом месте — поправь `accounts_file` и `perch_cli` в
`config.json`.

## Эндпоинты

- `GET /health` — пул: всего/живых/в полёте/кулдаун.
- `GET /v1/models` — 35 именованных моделей + `perch-auto`.
- `POST /v1/chat/completions` — OpenAI-формат, `stream=true` поддержан
  псевдо-стримом (чанки после завершения тура).

```bash
curl -s http://127.0.0.1:12066/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-opus-5.5","messages":[{"role":"user","content":"Say hi"}]}'
```

`model` передаётся в CLI как `--model`. Неизвестная модель → HTTP 400 со
списком из `/v1/models`. `perch-auto` — роутер Perch (на Starter обычно
`Qwen/Qwen3.6-35B-A3B`).

## Как работает

1. Аккаунты читаются из `accounts_file` (`data/accounts.jsonl` реггера):
   нужны `account_dir`, живая сессия `auth/cli-auth-session.json`,
   `live_ok != false`.
2. На запрос выбирается аккаунт с минимальным числом запросов в полёте.
3. Запуск: `node <perch> run "<prompt>" --json --mode ask --cwd <tmpdir>
   [--model <id>]` с `PERCH_CLI_AUTH_DIR` аккаунта и изолированным `HOME`.
4. История из OpenAI `messages` склеивается в один промпт (system +
   User/Assistant), ответ берётся из `assistantText`, usage — из
   `contextSnapshot.turnApiUsage`.
5. Ошибки `quota/usage limit/429/plan_model` → кулдаун аккаунта
   (`cooldown_sec`), повтор на другом аккаунте.
6. Расход пишется в `data/usage.jsonl` (его читает панель реггера).

## Настройки (`config.json`)

| Ключ | Смысл |
|---|---|
| `port` | порт (по умолчанию 12066), слушает `127.0.0.1` |
| `api_key` | если задан — требуется `Authorization: Bearer <key>` |
| `accounts_file` | путь к `accounts.jsonl` реггера |
| `perch_cli` | путь к бинарю `perch` (`.cli/node_modules/.bin/perch`) |
| `node_bin` | `node` |
| `timeout_sec` | таймаут одного тура (300) |
| `cooldown_sec` | пауза аккаунта после квоты/429 (600) |
| `max_prompt_chars` | обрезка истории (24000) |
| `models` | список разрешённых `model` |

## Ограничения

- **Латентность 15–40 с** на запрос (Node + агентный тур Perch).
- Один тур ~1.3–8k входных токенов системного контекста — Starter рассчитан
  на батч, не на интерактив.
- `stream=true` — псевдо-стрим, не настоящий SSE от модели.
- Точный PT-счётчик Perch не отдаёт публично; расход оценивается по
  `estimated_cost_usd` в `perch_ai_inference_usage`.

## Дисклеймер

Код «как есть», для личных/исследовательских задач. Соблюдайте ToS Perch.
