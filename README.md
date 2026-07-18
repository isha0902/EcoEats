# EcoEats

EcoEats is a small community marketplace for surplus or home-made food. Sellers post listings with pickup windows and quantities, and buyers can reserve and pick them up. It started as a learning project for building a full-stack app with Flask, but it's grown into a fairly complete little marketplace — authentication, roles, reservations, and CSRF protection included.

## Tech stack

- Python 3 + Flask
- Flask-SQLAlchemy for the database layer
- Flask-Login for authentication
- SQLite for local development
- Jinja2 templates, plain CSS/JS for the frontend

## What it does

- Sign up and log in as a buyer or seller
- Sellers can create, edit, and manage their own listings
- Buyers can browse, reserve, and track what they've reserved
- Old/legacy listings without an owner can be claimed by a seller
- CSRF protection on every form
- Config (secret key, database URL) comes from environment variables, not hardcoded

## Running it locally

The easiest way is the included setup script, which handles the virtual environment, dependencies, and seeding:

```bash
chmod +x run_local.sh   # only needed once
./run_local.sh
```

This sets up a `.venv`, installs everything in `requirements.txt`, seeds the database from `data/food.csv` if it's empty, and starts the Flask dev server.

If you'd rather do it by hand:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="dev-secret-change-me"
export DATABASE_URL="sqlite:///data/ecoeats.sqlite3"
python app.py
```

## Environment variables

- `SECRET_KEY` — used for sessions and CSRF tokens. Any string works for local dev; use something properly random in production.
- `DATABASE_URL` — defaults to a local SQLite file if not set. Also accepts a Postgres URL (`postgres://` gets normalized to `postgresql://` automatically).

```bash
export SECRET_KEY="replace-with-a-secret"
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
```

## Project layout

- `app.py` — routes, CSRF handling, app setup
- `db.py` — SQLAlchemy models: `User`, `Listing`, `Reservation`
- `templates/` — Jinja2 templates
- `static/` — CSS and JS
- `data/` — seed CSV and local SQLite DB
- `run_local.sh` — one-command local setup

## A note on security

CSRF protection here is a lightweight, session-based implementation I built myself — it works, but for a production app I'd swap in something battle-tested like Flask-WTF instead. Same goes for secrets: always pull them from environment variables, never commit them.