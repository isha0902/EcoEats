from __future__ import annotations

from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(190), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="buyer")  # buyer | seller
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)


class Listing(db.Model):
    __tablename__ = "listings"

    id = db.Column(db.String(32), primary_key=True)
    seller_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    title = db.Column(db.String(140), nullable=False)
    category = db.Column(db.String(60), nullable=False)

    # Keep these as strings for simplicity (easy formatting in templates).
    price = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)

    pickup_start = db.Column(db.String(32), nullable=False)  # ISO-like string (from datetime-local)
    pickup_end = db.Column(db.String(32), nullable=False)

    location = db.Column(db.String(140), nullable=False)
    seller_name = db.Column(db.String(140), nullable=False, default="")
    seller_contact = db.Column(db.String(140), nullable=False, default="")

    status = db.Column(db.String(16), nullable=False, default="available", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    seller = db.relationship("User", lazy="joined", foreign_keys=[seller_id])


class Reservation(db.Model):
    __tablename__ = "reservations"

    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.String(32), db.ForeignKey("listings.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    status = db.Column(db.String(16), nullable=False, default="active", index=True)  # active | cancelled | completed
    reserved_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    listing = db.relationship("Listing", lazy="joined")
    user = db.relationship("User", lazy="joined")

