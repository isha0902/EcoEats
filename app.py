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
from sqlalchemy import text, select

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

    @app.errorhandler(400)
    def bad_request(error):
        description = getattr(error, "description", "Bad request")
        return render_template("error.html", title="Bad request", message=description), 400

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            return db.session.get(User, int(user_id))
        except Exception:
            return None

    with app.app_context():
        db.create_all()
        ensure_schema_upgrades()
        backfill_seeded_listing_ownership()
        seed_db_from_csv_if_empty()

    @app.get("/")
    def home():
        listings = Listing.query.order_by(Listing.created_at.desc()).all()
        active_counts = get_active_reservation_counts([l.id for l in listings])
        active = [l for l in listings if l.status == "available"]
        return render_template(
            "index.html",
            stats={
                "total": len(listings),
                "available": len(active),
                "reserved": len([l for l in listings if l.status == "reserved"]),
            },
            latest=active[:6],
            active_reservation_counts=active_counts,
        )

    @app.get("/listings")
    def listings():
        q = (request.args.get("q") or "").strip().lower()
        category = (request.args.get("category") or "").strip().lower()
        status = (request.args.get("status") or "available").strip().lower()
        # pagination
        try:
            page = int(request.args.get("page", "1"))
        except ValueError:
            page = 1
        if page < 1:
            page = 1
        per_page = 12

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

        total = query.count()
        offset = (page - 1) * per_page
        total_pages = max((total + per_page - 1) // per_page, 1)
        if page > total_pages:
            page = total_pages
            offset = (page - 1) * per_page
        filtered = query.order_by(Listing.created_at.desc()).offset(offset).limit(per_page).all()
        active_counts = get_active_reservation_counts([l.id for l in filtered])

        categories = [c[0] for c in db.session.query(Listing.category).distinct().order_by(Listing.category.asc()).all()]
        my_reserved_ids: set[str] = set()
        if current_user.is_authenticated:
            my_reserved_ids = {
                rid
                for (rid,) in db.session.query(Reservation.listing_id)
                .filter(Reservation.user_id == int(current_user.get_id()), Reservation.status == "active")
                .all()
            }
        # pagination metadata
        return render_template(
            "listings.html",
            listings=filtered,
            q=q,
            category=category,
            status=status,
            categories=categories,
            my_reserved_ids=my_reserved_ids,
            page=page,
            total_pages=total_pages,
            per_page=per_page,
            total_count=total,
            active_reservation_counts=active_counts,
        )

    @app.get("/listings/new")
    @login_required
    def new_listing():
        if current_user.role != "seller":
            flash("Only sellers can post listings.", "error")
            return redirect(url_for("listings"))
        return render_template(
            "add_listing.html",
            form={},
            now_local=local_now_str(),
            page_title="Add a listing",
            page_subtitle="Post surplus food for nearby people to reserve.",
            submit_label="Create listing",
            cancel_url=url_for("listings"),
            action_url=url_for("create_listing_route"),
        )

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
            return render_template(
                "add_listing.html",
                form=form,
                now_local=local_now_str(),
                page_title="Add a listing",
                page_subtitle="Post surplus food for nearby people to reserve.",
                submit_label="Create listing",
                cancel_url=url_for("listings"),
                action_url=url_for("create_listing_route"),
            ), 400

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

    @app.get("/listings/<listing_id>/edit")
    @login_required
    def edit_listing(listing_id: str):
        if current_user.role != "seller":
            flash("Only sellers can edit listings.", "error")
            return redirect(url_for("listings"))

        listing = db.session.get(Listing, listing_id)
        if not listing:
            flash("Listing not found.", "error")
            return redirect_back()

        if listing.seller_id != int(current_user.get_id()):
            flash("You can only edit your own listings.", "error")
            return redirect_back()

        form = {
            "title": listing.title,
            "category": listing.category,
            "price": listing.price,
            "quantity": str(listing.quantity),
            "pickup_start": listing.pickup_start,
            "pickup_end": listing.pickup_end,
            "location": listing.location,
            "seller_name": listing.seller_name,
            "seller_contact": listing.seller_contact,
        }
        return render_template(
            "add_listing.html",
            form=form,
            now_local=local_now_str(),
            page_title="Edit listing",
            page_subtitle="Update the details for your listing.",
            submit_label="Save changes",
            cancel_url=url_for("my_listings"),
            action_url=url_for("update_listing", listing_id=listing.id),
        )

    @app.post("/listings/<listing_id>/edit")
    @login_required
    def update_listing(listing_id: str):
        if current_user.role != "seller":
            flash("Only sellers can edit listings.", "error")
            return redirect(url_for("listings"))

        listing = db.session.get(Listing, listing_id)
        if not listing:
            flash("Listing not found.", "error")
            return redirect_back()

        if listing.seller_id != int(current_user.get_id()):
            flash("You can only edit your own listings.", "error")
            return redirect_back()

        form = {k: (request.form.get(k) or "").strip() for k in request.form.keys()}
        errors = validate_listing_form(form)
        active_count = Reservation.query.filter_by(listing_id=listing_id, status="active").count()
        try:
            new_quantity = int(form.get("quantity", listing.quantity))
        except ValueError:
            new_quantity = listing.quantity
        if new_quantity < active_count:
            errors.append(f"Quantity cannot be less than the {active_count} active reservation(s).")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "add_listing.html",
                form=form,
                now_local=local_now_str(),
                page_title="Edit listing",
                page_subtitle="Update the details for your listing.",
                submit_label="Save changes",
                cancel_url=url_for("my_listings"),
                action_url=url_for("update_listing", listing_id=listing.id),
            ), 400

        listing.title = form["title"]
        listing.category = form["category"]
        listing.price = f"{float(form['price']):.2f}"
        listing.quantity = new_quantity
        listing.pickup_start = form["pickup_start"]
        listing.pickup_end = form["pickup_end"]
        listing.location = form["location"]
        listing.seller_name = form.get("seller_name", "")
        listing.seller_contact = form.get("seller_contact", "")
        db.session.commit()
        flash("Listing updated.", "success")
        return redirect(url_for("my_listings"))

    @app.post("/listings/<listing_id>/delete")
    @login_required
    def delete_listing(listing_id: str):
        if current_user.role != "seller":
            flash("Only sellers can delete listings.", "error")
            return redirect(url_for("listings"))

        listing = db.session.get(Listing, listing_id)
        if not listing:
            flash("Listing not found.", "error")
            return redirect_back()

        if listing.seller_id != int(current_user.get_id()):
            flash("You can only delete your own listings.", "error")
            return redirect_back()

        active_count = Reservation.query.filter_by(listing_id=listing_id, status="active").count()
        if active_count:
            flash("You cannot delete a listing with active reservations.", "error")
            return redirect_back()

        Reservation.query.filter_by(listing_id=listing_id).delete(synchronize_session=False)
        db.session.delete(listing)
        db.session.commit()
        flash("Listing deleted.", "success")
        return redirect(url_for("my_listings"))

    @app.post("/listings/<listing_id>/reserve")
    @login_required
    def reserve_listing(listing_id: str):
        try:
            with db.session.begin_nested():
                res = db.session.execute(select(Listing).filter_by(id=listing_id).with_for_update())
                listing = res.scalar_one_or_none()
                if not listing:
                    flash("Listing not found.", "error")
                    return redirect_back()

                if listing.status == "sold":
                    flash("This listing is not available.", "error")
                    return redirect_back()

                active_count = db.session.query(Reservation).filter_by(listing_id=listing_id, status="active").count()
                if active_count >= listing.quantity:
                    flash("This listing is fully reserved.", "error")
                    return redirect_back()

                reservation = Reservation(listing_id=listing_id, user_id=int(current_user.get_id()), status="active")
                db.session.add(reservation)
                db.session.flush()
                app.logger.info(f"Created reservation for listing {listing_id} by user {current_user.get_id()}")
                # mark reserved if full or partially reserved
                listing.status = "reserved" if active_count + 1 >= listing.quantity else "reserved"
            flash("Reserved.", "success")
            return redirect_back()
        except Exception as e:
            db.session.rollback()
            app.logger.exception(f"Error reserving listing {listing_id}: {e}")
            flash("Unable to reserve listing right now.", "error")
            return redirect_back()

    @app.post("/listings/<listing_id>/unreserve")
    @login_required
    def unreserve_listing(listing_id: str):
        try:
            with db.session.begin_nested():
                res = db.session.execute(select(Listing).filter_by(id=listing_id).with_for_update())
                listing = res.scalar_one_or_none()
                if not listing:
                    flash("Listing not found.", "error")
                    return redirect_back()

                reservation = db.session.query(Reservation).filter_by(listing_id=listing_id, user_id=int(current_user.get_id()), status="active").first()
                if reservation is None:
                    flash("You don't have an active reservation for this listing.", "error")
                    return redirect_back()

                reservation.status = "cancelled"
                db.session.flush()
                app.logger.info(f"Cancelled reservation {reservation.id} for listing {listing_id} by user {current_user.get_id()}")
                remaining = db.session.query(Reservation).filter_by(listing_id=listing_id, status="active").count()
                listing.status = "available" if remaining == 0 else "reserved"
            flash("Reservation cancelled.", "success")
            return redirect_back()
        except Exception as e:
            db.session.rollback()
            app.logger.exception(f"Error cancelling reservation {listing_id}: {e}")
            flash("Unable to cancel reservation right now.", "error")
            return redirect_back()

    @app.post("/listings/<listing_id>/claim")
    @login_required
    def claim_listing(listing_id: str):
        if current_user.role != "seller":
            flash("Only sellers can claim listings.", "error")
            return redirect_back()

        listing = db.session.get(Listing, listing_id)
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
        try:
            with db.session.begin_nested():
                res = db.session.execute(select(Listing).filter_by(id=listing_id).with_for_update())
                listing = res.scalar_one_or_none()
                if not listing:
                    flash("Listing not found.", "error")
                    return redirect_back()

                # Strict ownership check: listings with no owner cannot be sold from UI actions.
                if listing.seller_id != int(current_user.get_id()):
                    flash("You can only mark your own listings as sold.", "error")
                    return redirect_back()

                listing.status = "sold"
                # mark all active reservations completed
                active_reservations = db.session.query(Reservation).filter_by(listing_id=listing_id, status="active").all()
                for active_res in active_reservations:
                    active_res.status = "completed"
            flash("Marked as sold.", "success")
            return redirect_back()
        except Exception as e:
            db.session.rollback()
            app.logger.exception(f"Error marking sold {listing_id}: {e}")
            flash("Unable to mark sold right now.", "error")
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
        active_counts = get_active_reservation_counts([l.id for l in listings])
        return render_template("my_listings.html", listings=listings, active_reservation_counts=active_counts)

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
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
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


def get_active_reservation_counts(listing_ids: list[str]) -> dict[str, int]:
    if not listing_ids:
        return {}

    rows = (
        db.session.query(Reservation.listing_id, db.func.count(Reservation.id))
        .filter(Reservation.listing_id.in_(listing_ids), Reservation.status == "active")
        .group_by(Reservation.listing_id)
        .all()
    )
    return {listing_id: count for listing_id, count in rows}


def update_status(listing_id: str, status: str) -> bool:
    listing = db.session.get(Listing, listing_id)
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
        seller_cache: dict[tuple[str, str], User] = {}
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
                if db.session.get(Listing, rid) is not None:
                    continue

                try:
                    q_int = int(quantity) if quantity else 1
                except ValueError:
                    q_int = 1

                seller_user = get_or_create_seed_seller_user(seller_name, seller_contact, seller_cache)

                listing = Listing(
                    id=rid,
                    seller_id=seller_user.id if seller_user is not None else None,
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


def backfill_seeded_listing_ownership() -> None:
    """Attach seller ownership to existing seeded listings when possible."""
    seller_cache: dict[tuple[str, str], User] = {}
    changed = False
    for listing in Listing.query.filter(Listing.seller_id.is_(None)).all():
        if not listing.seller_name and not listing.seller_contact:
            continue
        seller_user = get_or_create_seed_seller_user(listing.seller_name, listing.seller_contact, seller_cache)
        if seller_user is None:
            continue
        listing.seller_id = seller_user.id
        changed = True
    if changed:
        db.session.commit()


def get_or_create_seed_seller_user(
    seller_name: str,
    seller_contact: str,
    seller_cache: dict[tuple[str, str], User],
) -> User | None:
    seller_name = (seller_name or "").strip()
    seller_contact = (seller_contact or "").strip().lower()
    if not seller_name and not seller_contact:
        return None

    cache_key = (seller_name.lower(), seller_contact)
    if cache_key in seller_cache:
        return seller_cache[cache_key]

    if seller_contact and "@" in seller_contact:
        email = seller_contact
    else:
        base = seller_name or seller_contact or "seeded-seller"
        slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in base).strip("-") or "seeded-seller"
        email = f"{slug}@ecoeats.local"

    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            name=seller_name or seller_contact or "Seeded Seller",
            password_hash=generate_password_hash(secrets.token_urlsafe(16)),
            role="seller",
        )
        db.session.add(user)
        db.session.flush()

    seller_cache[cache_key] = user
    return user


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
