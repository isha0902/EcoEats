# EcoEats (Flask MVP)

EcoEats is a small starter app that reduces food waste by letting people post surplus food listings and letting others reserve pickups.

## Features

- Browse listings with search + category + status filters
- Post a new listing (stored in SQLite)
- Reserve / unreserve / mark sold
- Simple, modern UI (templates + static assets)

## Project structure

```text
.
├─ app.py
├─ requirements.txt
├─ data/
│  ├─ ecoeats.sqlite3
│  └─ food.csv
├─ templates/
│  ├─ base.html
│  ├─ index.html
│  ├─ listings.html
│  └─ add_listing.html
└─ static/
   ├─ styles.css
   └─ app.js
```

## Run locally (macOS / zsh)

```bash
cd /Users/ishashreya/Desktop/ECOEATS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask --app app run --debug
```

Open `http://127.0.0.1:5000`.

## Deploy (Render)

This repo includes:

- `Procfile` (gunicorn start command)
- `render.yaml` (optional infrastructure definition)

Render start command:

```bash
gunicorn app:app
```

## Notes

- Data lives in `data/ecoeats.sqlite3` (SQLite).
- If `data/ecoeats.sqlite3` is empty and `data/food.csv` exists, the app will import the CSV once on startup.
- For production, replace the `secret_key` in `app.py`.
