import re
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app import create_app
from db import db, Listing, Reservation, User


@pytest.fixture
def client(monkeypatch):
    # Use a temporary file-backed sqlite DB for tests (avoids in-memory connection issues)
    test_db_path = "data/test_ecoeats.sqlite3"
    # ensure clean state
    try:
        import os
        if os.path.exists(test_db_path):
            os.remove(test_db_path)
        # ensure parent directory exists
        os.makedirs(os.path.dirname(test_db_path), exist_ok=True)
        abs_path = os.path.abspath(test_db_path)
    except Exception:
        pass
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{abs_path}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def _extract_csrf(resp):
    m = re.search(b'name="_csrf_token" value="([^"]+)"', resp.data)
    return m.group(1).decode() if m else None


def signup(client, email, name, password, role="buyer"):
    rv = client.get("/signup")
    token = _extract_csrf(rv)
    data = {
        "_csrf_token": token,
        "email": email,
        "name": name,
        "password": password,
        "role": role,
    }
    return client.post("/signup", data=data, follow_redirects=True)


def login(client, email, password):
    rv = client.get("/login")
    token = _extract_csrf(rv)
    data = {"_csrf_token": token, "email": email, "password": password}
    return client.post("/login", data=data, follow_redirects=True)


def test_health(client):
    rv = client.get("/health")
    assert rv.status_code == 200
    assert rv.json.get("ok") is True


def test_signup_valid_and_duplicate(client):
    # valid signup
    r = signup(client, "dup@example.com", "Dup", "password123", role="buyer")
    assert r.status_code == 200
    assert b"Account created." in r.data

    # duplicate signup should fail
    # logout first so we can access signup page again
    rv = client.get("/")
    token = _extract_csrf(rv)
    client.post("/logout", data={"_csrf_token": token}, follow_redirects=True)

    rv = client.get("/signup")
    token = _extract_csrf(rv)
    data = {"_csrf_token": token, "email": "dup@example.com", "name": "Dup2", "password": "password123"}
    post = client.post("/signup", data=data)
    assert post.status_code == 400
    assert b"An account with that email already exists." in post.data


def test_login_correct_and_incorrect(client):
    signup(client, "login@example.com", "LoginUser", "mypassword", role="buyer")
    # logout so we can test login flow
    rv = client.get("/")
    token = _extract_csrf(rv)
    client.post("/logout", data={"_csrf_token": token}, follow_redirects=True)

    # incorrect password
    bad = login(client, "login@example.com", "wrongpass")
    assert bad.status_code == 401
    assert b"Invalid email or password." in bad.data

    # correct password
    good = login(client, "login@example.com", "mypassword")
    assert good.status_code == 200
    assert b"Welcome back." in good.data


def test_create_listing_as_seller_and_claim_and_reserve(client):
    # create a seller account and log in
    signup(client, "seller@example.com", "Seller", "sellerpass", role="seller")
    login(client, "seller@example.com", "sellerpass")

    # get CSRF from new listing page
    rv = client.get("/listings/new")
    token = _extract_csrf(rv)
    assert token

    now = datetime.now()
    start = now.isoformat()
    end = (now + timedelta(hours=1)).isoformat()

    listing_data = {
        "_csrf_token": token,
        "title": "Test Apples",
        "category": "Fruit",
        "price": "3.50",
        "quantity": "2",
        "pickup_start": start,
        "pickup_end": end,
        "location": "Community Center",
        "seller_name": "Seller",
        "seller_contact": "555-1234",
    }

    post = client.post("/listings", data=listing_data, follow_redirects=True)
    assert post.status_code == 200
    assert b"Listing created." in post.data

    # Create an unassigned listing directly for claim/reserve tests
    listing_id = uuid4().hex
    with client.application.app_context():
        l = Listing(
            id=listing_id,
            title="Unassigned Bread",
            category="Bakery",
            price="2.00",
            quantity=1,
            pickup_start=start,
            pickup_end=end,
            location="Market",
            seller_name="",
            seller_contact="",
            status="available",
        )
        db.session.add(l)
        db.session.commit()

    # log out seller
    rv = client.get("/")
    token = _extract_csrf(rv)
    client.post("/logout", data={"_csrf_token": token}, follow_redirects=True)

    # Create a second seller and claim the unassigned listing
    signup(client, "seller2@example.com", "Seller2", "seller2pass", role="seller")
    login(client, "seller2@example.com", "seller2pass")
    rv = client.get("/listings")
    token = _extract_csrf(rv)
    claim_post = client.post(f"/listings/{listing_id}/claim", data={"_csrf_token": token}, follow_redirects=True)
    assert claim_post.status_code == 200
    assert b"Listing claimed." in claim_post.data

    # Reserve an available listing as a buyer
    # create buyer
    client.post("/logout", data={"_csrf_token": token}, follow_redirects=True)
    signup(client, "buyer@example.com", "Buyer", "buyerpass", role="buyer")
    login(client, "buyer@example.com", "buyerpass")

    # reserve the previously created listing (the one created by seller via form or direct)
    rv = client.get("/listings")
    token = _extract_csrf(rv)
    # reserve the listing we created earlier (unassigned bread) which is now assigned to seller2
    reserve_resp = client.post(f"/listings/{listing_id}/reserve", data={"_csrf_token": token}, follow_redirects=True)
    assert reserve_resp.status_code == 200
    # verify reservation exists in DB
    with client.application.app_context():
        res = Reservation.query.filter_by(listing_id=listing_id).first()
        assert res is not None


def test_quantity_limits_reservations(client):
    signup(client, "cap@example.com", "Cap Seller", "capseller", role="seller")
    login(client, "cap@example.com", "capseller")

    now = datetime.now()
    start = now.isoformat()
    end = (now + timedelta(hours=1)).isoformat()
    listing_id = uuid4().hex

    with client.application.app_context():
        seller = User.query.filter_by(email="cap@example.com").first()
        db.session.add(
            Listing(
                id=listing_id,
                seller_id=seller.id,
                title="Capacity Test",
                category="Prepared Meals",
                price="5.00",
                quantity=2,
                pickup_start=start,
                pickup_end=end,
                location="Test Market",
                seller_name="Cap Seller",
                seller_contact="cap@example.com",
                status="available",
            )
        )
        db.session.commit()

    client.post("/logout", data={"_csrf_token": _extract_csrf(client.get("/"))}, follow_redirects=True)

    signup(client, "buyer1@example.com", "Buyer1", "buyerpass1", role="buyer")
    login(client, "buyer1@example.com", "buyerpass1")
    reserve1 = client.post(f"/listings/{listing_id}/reserve", data={"_csrf_token": _extract_csrf(client.get("/"))}, follow_redirects=True)
    assert reserve1.status_code == 200
    with client.application.app_context():
        r1 = Reservation.query.filter_by(listing_id=listing_id, user_id=User.query.filter_by(email="buyer1@example.com").first().id).first()
        assert r1 is not None

    client.post("/logout", data={"_csrf_token": _extract_csrf(client.get("/"))}, follow_redirects=True)

    signup(client, "buyer2@example.com", "Buyer2", "buyerpass2", role="buyer")
    login(client, "buyer2@example.com", "buyerpass2")
    reserve2 = client.post(f"/listings/{listing_id}/reserve", data={"_csrf_token": _extract_csrf(client.get("/"))}, follow_redirects=True)
    assert reserve2.status_code == 200
    assert b"Reserved." in reserve2.data

    client.post("/logout", data={"_csrf_token": _extract_csrf(client.get("/"))}, follow_redirects=True)

    signup(client, "buyer3@example.com", "Buyer3", "buyerpass3", role="buyer")
    login(client, "buyer3@example.com", "buyerpass3")
    reserve3 = client.post(f"/listings/{listing_id}/reserve", data={"_csrf_token": _extract_csrf(client.get("/"))}, follow_redirects=True)
    assert reserve3.status_code == 200
    assert b"fully reserved" in reserve3.data

    with client.application.app_context():
        listing = db.session.get(Listing, listing_id)
        active_count = Reservation.query.filter_by(listing_id=listing_id, status="active").count()
        assert listing.status == "reserved"
        assert active_count == 2


def test_edit_and_delete_listing_flow(client):
    signup(client, "editor@example.com", "Editor", "editorpass", role="seller")
    login(client, "editor@example.com", "editorpass")

    now = datetime.now()
    start = now.isoformat()
    end = (now + timedelta(hours=1)).isoformat()

    rv = client.get("/listings/new")
    token = _extract_csrf(rv)
    create_resp = client.post(
        "/listings",
        data={
            "_csrf_token": token,
            "title": "Editable Soup",
            "category": "Prepared Meals",
            "price": "6.00",
            "quantity": "3",
            "pickup_start": start,
            "pickup_end": end,
            "location": "Kitchen",
            "seller_name": "Editor",
            "seller_contact": "editor@example.com",
        },
        follow_redirects=True,
    )
    assert create_resp.status_code == 200

    with client.application.app_context():
        listing = Listing.query.filter_by(title="Editable Soup").first()
        assert listing is not None
        listing_id = listing.id

    edit_page = client.get(f"/listings/{listing_id}/edit")
    assert edit_page.status_code == 200
    edit_token = _extract_csrf(edit_page)
    update_resp = client.post(
        f"/listings/{listing_id}/edit",
        data={
            "_csrf_token": edit_token,
            "title": "Updated Soup",
            "category": "Prepared Meals",
            "price": "7.25",
            "quantity": "4",
            "pickup_start": start,
            "pickup_end": end,
            "location": "Updated Kitchen",
            "seller_name": "Editor",
            "seller_contact": "editor@example.com",
        },
        follow_redirects=True,
    )
    assert update_resp.status_code == 200
    assert b"Listing updated." in update_resp.data

    with client.application.app_context():
        updated = db.session.get(Listing, listing_id)
        assert updated.title == "Updated Soup"
        assert updated.price == "7.25"
        assert updated.quantity == 4

    delete_token = _extract_csrf(client.get("/"))
    delete_resp = client.post(
        f"/listings/{listing_id}/delete",
        data={"_csrf_token": delete_token},
        follow_redirects=True,
    )
    assert delete_resp.status_code == 200
    assert b"Listing deleted." in delete_resp.data

    with client.application.app_context():
        assert db.session.get(Listing, listing_id) is None


def test_listings_pagination_preserves_filters(client):
    signup(client, "pager@example.com", "Pager", "pagerpass", role="seller")
    login(client, "pager@example.com", "pagerpass")

    now = datetime.now()
    start = now.isoformat()
    end = (now + timedelta(hours=1)).isoformat()

    with client.application.app_context():
        seller = User.query.filter_by(email="pager@example.com").first()
        for i in range(13):
            db.session.add(
                Listing(
                    id=uuid4().hex,
                    seller_id=seller.id,
                    title=f"Paginated Item {i + 1}",
                    category="Fruit",
                    price="1.00",
                    quantity=1,
                    pickup_start=start,
                    pickup_end=end,
                    location="Test Market",
                    seller_name="Pager",
                    seller_contact="pager@example.com",
                    status="available",
                )
            )
        db.session.commit()

    page1 = client.get("/listings?status=available&category=fruit&q=paginated")
    assert page1.status_code == 200
    page1_html = page1.get_data(as_text=True)
    assert "Paginated Item 13" in page1_html
    assert "Paginated Item 1" in page1_html
    assert "Next →" in page1_html or "Next" in page1_html
    assert "Last" in page1_html
    assert 'aria-current="page">1<' in page1_html

    page2 = client.get("/listings?status=available&category=fruit&q=paginated&page=2")
    assert page2.status_code == 200
    page2_html = page2.get_data(as_text=True)
    assert "Paginated Item 13" not in page2_html
    assert "Paginated Item 1" in page2_html
    assert 'aria-current="page">2<' in page2_html


def test_seeded_listings_keep_owner_assignment(client):
    with client.application.app_context():
        seeded_listing = Listing.query.filter_by(id="9c7d1a5b4e8b4f7a8b8d4c1e5f2e9d10").first()
        assert seeded_listing is not None
        assert seeded_listing.seller_id is not None
        assert seeded_listing.seller is not None
        assert seeded_listing.seller.role == "seller"


def test_listings_page_clamps_past_last_page(client):
    signup(client, "clamp@example.com", "Clamp", "clampable", role="seller")
    login(client, "clamp@example.com", "clampable")

    now = datetime.now()
    start = now.isoformat()
    end = (now + timedelta(hours=1)).isoformat()

    with client.application.app_context():
        seller = User.query.filter_by(email="clamp@example.com").first()
        for i in range(13):
            db.session.add(
                Listing(
                    id=uuid4().hex,
                    seller_id=seller.id,
                    title=f"Clamp Item {i + 1}",
                    category="Fruit",
                    price="1.00",
                    quantity=1,
                    pickup_start=start,
                    pickup_end=end,
                    location="Test Market",
                    seller_name="Clamp",
                    seller_contact="clamp@example.com",
                    status="available",
                )
            )
        db.session.commit()

    response = client.get("/listings?page=99&status=available&category=fruit&q=clamp")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Clamp Item 13" not in html
    assert "Clamp Item 1" in html
    assert 'aria-current="page">2<' in html


def test_invalid_csrf_returns_friendly_error_page(client):
    signup(client, "csrf@example.com", "CSRF", "csrfpass8", role="buyer")

    response = client.post(
        "/logout",
        data={"_csrf_token": "invalid-token"},
        follow_redirects=True,
    )

    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "Bad request" in html
    assert "Invalid CSRF token" in html
    assert "Back home" in html
