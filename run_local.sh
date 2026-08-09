#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [[ ! -f ".venv/.deps_installed" ]]; then
  pip install -r requirements.txt
  touch .venv/.deps_installed
fi

export SECRET_KEY="${SECRET_KEY:-local-dev-secret}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///$PWD/data/ecoeats.sqlite3}"

python3 app.py
