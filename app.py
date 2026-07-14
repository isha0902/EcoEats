from __future__ import annotations

import csv
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from db import Listing, Reservation, User, db
from sqlalchemy import text

APP_ROOT = Path(__file__).parent.resolve()
DATA_PATH = APP_ROOT / "data" / "food.csv"
DB_PATH = APP_ROOT / "data" / "ecoeats.sqlite3"
CSRF_SESSION_KEY = "_csrf_token"


def get_database_uri() -> str:
    # Prefer platform-provided DATABASE_URL in production, with sqlite fallback for local dev.
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return f"sqlite:///{DB_PATH}"
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql://", 1)
    return database_url


def generate_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def is_valid_csrf_token(token: str) -> bool:
    expected = session.get(CSRF_SESSION_KEY)
    return bool(token and expected and secrets.compare_digest(token, expected))


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
    app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": generate_csrf_token}

    @app.before_request
    def csrf_protect():
        if request.method == "POST":
            token = request.form.get("_csrf_token", "")
            if not is_valid_csrf_token(token):
                abort(400, description="Invalid CSRF token")

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            return User.query.get(int(user_id))
        except Exception:
            return None

    with app.app_context():
        db.create_all()
        ensure_schema_upgrades()
        seed_db_from_csv_if_empty()

    @app.get("/")
    def home():
        listings = Listing.query.order_by(Listing.created_at.desc()).all()
        active = [l for l in listings if l.status == "available"]
        return render_template(
            "index.html",
            stats={
                "total": len(listings),
                "available": len(active),
                "reserved": len([l for l in listings if l.status == "reserved"]),
            },
            latest=active[:6],
        )

    @app.get("/listings")
    def listings():
        q = (request.args.get("q") or "").strip().lower()
        category = (request.args.get("category") or "").strip().lower()
        status = (request.args.get("status") or "available").strip().lower()

        query = Listing.query
        if status in {"available", "reserved", "sold"}:
            query = query.filter(Listing.status == status)

        if category:
            query = query.filter(db.func.lower(Listing.category) == category)

        if q:
            like = f"%{q}%"
            query = query.filter(
                db.or_(
                    Listing.title.ilike(like),
                    Listing.category.ilike(like),
                    Listing.location.ilike(like),
                    Listing.seller_name.ilike(like),
                )
            )

        filtered = query.order_by(Listing.created_at.desc()).all()

        categories = [c[0] for c in db.session.query(Listing.category).distinct().order_by(Listing.category.asc()).all()]
        my_reserved_ids: set[str] = set()
        if current_user.is_authenticated:
            my_reserved_ids = {
                rid
                for (rid,) in db.session.query(Reservation.listing_id)
                .filter(Reservation.user_id == int(current_user.get_id()), Reservation.status == "active")
                .all()
            }
        return render_template(
            "listings.html",
            listings=filtered,
            q=q,
            category=category,
            status=status,
            categories=categories,
            my_reserved_ids=my_reserved_ids,
        )

    @app.get("/listings/new")
    @login_required
    def new_listing():
        if current_user.role != "seller":
            flash("Only sellers can post listings.", "error")
            return redirect(url_for("listings"))
        return render_template("add_listing.html", form={}, now_local=local_now_str())

    @app.post("/listings")
    @login_required
    def create_listing_route():
        if current_user.role != "seller":
            flash("Only sellers can post listings.", "error")
            return redirect(url_for("listings"))
        form = {k: (request.form.get(k) or "").strip() for k in request.form.keys()}
        errors = validate_listing_form(form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("add_listing.html", form=form, now_local=local_now_str()), 400

        listing_data = normalize_listing(form)
        listing = Listing(
            id=listing_data["id"],
            seller_id=int(current_user.get_id()),
            title=listing_data["title"],
            category=listing_data["category"],
            price=listing_data["price"],
            quantity=int(listing_data["quantity"]),
            pickup_start=listing_data["pickup_start"],
            pickup_end=listing_data["pickup_end"],
            location=listing_data["location"],
            seller_name=listing_data["seller_name"],
            seller_contact=listing_data["seller_contact"],
            status=listing_data["status"],
        )
        db.session.add(listing)
        db.session.commit()
        flash("Listing created.", "success")
        return redirect(url_for("listings"))

    @app.post("/listings/<listing_id>/reserve")
    @login_required
    def reserve_listing(listing_id: str):
        listing = Listing.query.get(listing_id)
        if not listing:
            flash("Listing not found.", "error")
            return redirect_back()

        if listing.status != "available":
            flash("This listing is not available.", "error")
            return redirect_back()

        existing = Reservation.query.filter_by(listing_id=listing_id, status="active").first()
        if existing is not None:
            flash("This listing is already reserved.", "error")
            return redirect_back()

        reservation = Reservation(listing_id=listing_id, user_id=int(current_user.get_id()), status="active")
        db.session.add(reservation)
        listing.status = "reserved"
        db.session.commit()
        flash("Reserved.", "success")
        return redirect_back()

    @app.post("/listings/<listing_id>/unreserve")
    @login_required
    def unreserve_listing(listing_id: str):
        listing = Listing.query.get(listing_id)
        if not listing:
            flash("Listing not found.", "error")
            return redirect_back()

        reservation = Reservation.query.filter_by(listing_id=listing_id, user_id=int(current_user.get_id()), status="active").first()
        if reservation is None:
            flash("You don't have an active reservation for this listing.", "error")
            return redirect_back()

        reservation.status = "cancelled"
        listing.status = "available"
        db.session.commit()
        flash("Reservation cancelled.", "success")
        return redirect_back()

    @app.post("/listings/<listing_id>/claim")
    @login_required
    def claim_listing(listing_id: str):
        if current_user.role != "seller":
            flash("Only sellers can claim listings.", "error")
            return redirect_back()

        listing = Listing.query.get(listing_id)
        if not listing:
            flash("Listing not found.", "error")
            return redirect_back()

        current_user_id = int(current_user.get_id())
        if listing.seller_id == current_user_id:
            flash("This listing is already assigned to you.", "success")
            return redirect_back()

        if listing.seller_id is not None and listing.seller_id != current_user_id:
            flash("This listing is already assigned to another seller.", "error")
            return redirect_back()

        listing.seller_id = current_user_id
        db.session.commit()
        flash("Listing claimed. You can now manage it.", "success")
        return redirect_back()

    @app.post("/listings/<listing_id>/sold")
    @login_required
    def mark_sold(listing_id: str):
        if current_user.role != "seller":
            flash("Only sellers can mark items as sold.", "error")
            return redirect_back()
        listing = Listing.query.get(listing_id)
        if not listing:
            flash("Listing not found.", "error")
            return redirect_back()
        # Strict ownership check: listings with no owner cannot be sold from UI actions.
        if listing.seller_id != int(current_user.get_id()):
            flash("You can only mark your own listings as sold.", "error")
            return redirect_back()

        listing.status = "sold"
        # If it was reserved, mark that active reservation completed.
        active_res = Reservation.query.filter_by(listing_id=listing_id, status="active").first()
        if active_res is not None:
            active_res.status = "completed"
        db.session.commit()
        flash("Marked as sold.", "success")
        return redirect_back()

    @app.get("/health")
    def health():
        return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}

    @app.get("/reservations")
    @login_required
    def my_reservations():
        reservations = (
            Reservation.query.filter_by(user_id=int(current_user.get_id()))
            .order_by(Reservation.reserved_at.desc())
            .all()
        )
        return render_template("reservations.html", reservations=reservations)

    @app.get("/my-listings")
    @login_required
    def my_listings():
        if current_user.role != "seller":
            flash("Only sellers have listings.", "error")
            return redirect(url_for("listings"))
        listings = (
            Listing.query.filter_by(seller_id=int(current_user.get_id()))
            .order_by(Listing.created_at.desc())
            .all()
        )
        return render_template("my_listings.html", listings=listings)

    @app.get("/signup")
    def signup():
        if current_user.is_authenticated:
            return redirect(url_for("home"))
        return render_template("signup.html")

    @app.post("/signup")
    def signup_post():
        if current_user.is_authenticated:
            return redirect(url_for("home"))

        email = (request.form.get("email") or "").strip().lower()
        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        role = (request.form.get("role") or "buyer").strip().lower()
        if role not in {"buyer", "seller"}:
            role = "buyer"

        errors = []
        if not email or "@" not in email:
            errors.append("Enter a valid email.")
        if not name:
            errors.append("Enter your name.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if User.query.filter_by(email=email).first() is not None:
            errors.append("An account with that email already exists.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("signup.html"), 400

        user = User(
            email=email,
            name=name,
            password_hash=generate_password_hash(password),
            role=role,
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Account created.", "success")
        return redirect(url_for("home"))

    @app.get("/login")
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("home"))
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        if current_user.is_authenticated:
            return redirect(url_for("home"))

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        if user is None or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.", "error")
            return render_template("login.html"), 401

        login_user(user)
        flash("Welcome back.", "success")
        return redirect(url_for("home"))

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Logged out.", "success")
        return redirect(url_for("home"))

    return app


def redirect_back():
    return redirect(request.referrer or url_for("listings"))


def update_status(listing_id: str, status: str) -> bool:
    listing = Listing.query.get(listing_id)
    if not listing:
        return False
    listing.status = status
    db.session.commit()
    return True


def seed_db_from_csv_if_empty() -> None:
    # One-time convenience: if the database has no rows but food.csv exists, import it.
    if Listing.query.first() is not None:
        return

    if not DATA_PATH.exists():
        return

    try:
        with DATA_PATH.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            imported = 0
            for r in reader:
                rid = (r.get("id") or "").strip()
                title = (r.get("title") or "").strip()
                category = (r.get("category") or "").strip()
                price = (r.get("price") or "").strip()
                quantity = (r.get("quantity") or "").strip()
                pickup_start = (r.get("pickup_start") or "").strip()
                pickup_end = (r.get("pickup_end") or "").strip()
                location = (r.get("location") or "").strip()
                seller_name = (r.get("seller_name") or "").strip()
                seller_contact = (r.get("seller_contact") or "").strip()
                status = (r.get("status") or "available").strip()

                if not rid or not title:
                    continue
                if Listing.query.get(rid) is not None:
                    continue

                try:
                    q_int = int(quantity) if quantity else 1
                except ValueError:
                    q_int = 1

                listing = Listing(
                    id=rid,
                    title=title,
                    category=category or "Other",
                    price=price or "0.00",
                    quantity=q_int,
                    pickup_start=pickup_start or "",
                    pickup_end=pickup_end or "",
                    location=location or "",
                    seller_name=seller_name or "",
                    seller_contact=seller_contact or "",
                    status=status if status in {"available", "reserved", "sold"} else "available",
                )
                db.session.add(listing)
                imported += 1

            if imported:
                db.session.commit()
    except Exception:
        db.session.rollback()
        # If import fails, app still runs; user can add listings manually.
        return


def ensure_schema_upgrades() -> None:
    # Lightweight migrations for SQLite (no Alembic in this starter).
    insp = db.inspect(db.engine)
    cols = {c["name"] for c in insp.get_columns("listings")}
    if "seller_id" not in cols:
        db.session.execute(text("ALTER TABLE listings ADD COLUMN seller_id INTEGER"))
        db.session.commit()


def validate_listing_form(form: dict[str, str]) -> list[str]:
    errors: list[str] = []
    required = ["title", "category", "price", "quantity", "pickup_start", "pickup_end", "location"]
    for k in required:
        if not form.get(k):
            errors.append(f"Missing {k.replace('_', ' ')}.")

    try:
        if form.get("price"):
            float(form["price"])
    except ValueError:
        errors.append("Price must be a number.")

    try:
        if form.get("quantity"):
            q = int(form["quantity"])
            if q < 1:
                errors.append("Quantity must be at least 1.")
    except ValueError:
        errors.append("Quantity must be an integer.")

    for k in ["pickup_start", "pickup_end"]:
        if form.get(k):
            try:
                datetime.fromisoformat(form[k])
            except ValueError:
                errors.append(f"{k.replace('_', ' ')} must be ISO datetime (use the picker).")

    if form.get("pickup_start") and form.get("pickup_end"):
        try:
            start = datetime.fromisoformat(form["pickup_start"])
            end = datetime.fromisoformat(form["pickup_end"])
            if end <= start:
                errors.append("Pickup end must be after pickup start.")
        except ValueError:
            pass

    return errors


def normalize_listing(form: dict[str, str]) -> dict[str, str]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": uuid.uuid4().hex,
        "title": form.get("title", ""),
        "category": form.get("category", ""),
        "price": f"{float(form.get('price', '0') or 0):.2f}",
        "quantity": str(int(form.get("quantity", "1") or 1)),
        "pickup_start": form.get("pickup_start", ""),
        "pickup_end": form.get("pickup_end", ""),
        "location": form.get("location", ""),
        "seller_name": form.get("seller_name", ""),
        "seller_contact": form.get("seller_contact", ""),
        "status": "available",
        "created_at": now,
    }


def local_now_str() -> str:
    now = datetime.now().replace(microsecond=0)
    return now.isoformat()


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
