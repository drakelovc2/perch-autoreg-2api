#!/usr/bin/env bash
# Синхронизация пула Perch с VPS (реггер живёт там, мост может работать локально).
# Запуск: VPS=user@host bash tools/sync-from-vps.sh
set -euo pipefail
VPS="${VPS:?set VPS=user@host}"
REMOTE="${REMOTE:-/opt/perch-register}"
LOCAL="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$LOCAL/data/accounts"
rsync -az "$VPS:$REMOTE/data/accounts.jsonl" "$LOCAL/data/accounts.jsonl.vps"
rsync -az "$VPS:$REMOTE/data/accounts/" "$LOCAL/data/accounts/"
LOCAL_ROOT="$LOCAL/data/accounts" python3 - <<'PY'
import json, os
root = os.environ["LOCAL_ROOT"]
out = []
for line in open(os.path.join(os.path.dirname(root), "accounts.jsonl.vps"),
                 encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    email = r.get("email") or ""
    r["account_dir"] = os.path.join(root, email)
    r["session_file"] = os.path.join(r["account_dir"], "auth",
                                     "cli-auth-session.json")
    r["synced_from_vps"] = True
    out.append(json.dumps(r, ensure_ascii=False))
open(os.path.join(os.path.dirname(root), "accounts.jsonl"), "w",
     encoding="utf-8").write("\n".join(out) + "\n")
print(f"синхронизировано аккаунтов: {len(out)}")
PY
