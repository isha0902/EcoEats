# EcoEats

EcoEats is a simple community marketplace that connects local sellers with buyers for surplus or home-made food items. Sellers can post listings with pickup windows and quantities; buyers can reserve items and complete purchases. The app is intended as a lightweight starter project for learning web development with Flask and small-scale marketplaces.

## Tech stack

- Python 3
- Flask (server and templating)
- Flask-SQLAlchemy (ORM)
- Flask-Login (authentication)
- SQLite (default dev database)
- HTML / Jinja2 templates, CSS, and minimal JavaScript for UI

## Key features

- User authentication (signup/login)
- Role-based access: buyers and sellers
- Create, browse, and manage listings (sellers)
- Reserve items and view reservations (buyers)
- Claim unassigned/legacy listings (sellers)
- Lightweight CSRF protection for POST forms
- Environment-driven configuration (SECRET_KEY, DATABASE_URL)

## Quick setup (recommended)

The repository includes a helper script `run_local.sh` to create a virtual environment, install dependencies, set reasonable default env vars for local development, and start the app.

From the project root:

```bash
chmod +x run_local.sh   # only required once
./run_local.sh
```

The script will:
- create or reuse a `.venv` virtual environment
- install packages from `requirements.txt`
- seed the database from `data/food.csv` if the database is empty
- start the Flask development server

If you prefer to run manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="dev-secret-change-me"
export DATABASE_URL="sqlite:///data/ecoeats.sqlite3"
python app.py
```

By default the app uses `data/ecoeats.sqlite3` for local development. If `DATABASE_URL` points to an empty database the app will seed initial listings from `data/food.csv`.

## Environment variables

- `SECRET_KEY` (required) — Flask secret key used for sessions and CSRF tokens. For development you can use a simple string, but in production use a secure random value.
- `DATABASE_URL` (optional) — SQLAlchemy database URL. Defaults to `sqlite:///data/ecoeats.sqlite3`. The app normalizes `postgres://` → `postgresql://` if needed.

Example:

```bash
export SECRET_KEY="replace-with-a-secret"
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
```

## Project layout

- `app.py` — Flask application, routes, CSRF helper, and initialization
- `db.py` — SQLAlchemy models (`User`, `Listing`, `Reservation`) and helpers
- `templates/` — Jinja2 templates for pages and forms
- `static/` — CSS and JavaScript
- `data/` — bundled CSV seed and development SQLite DB
- `run_local.sh` — convenience script to set up and run locally

## Security & notes

- A lightweight, session-backed CSRF protection is implemented; all POST forms include a CSRF token. This is sufficient for a learning/demo app but consider integrating a mature library (e.g., Flask-WTF) for production workloads.
- Environment variables should be used for secrets and production database connections. Do not commit credentials to source control.

## Screenshots

_Add screenshots here later (e.g., UI snapshots of the listings page, add listing flow, reservations)._ 

## Live demo

_Paste live demo URL here._

---
<<<<<<< HEAD

=======
>>>>>>> a242ae4ba496d59ed47796901bebe46f716e098c
