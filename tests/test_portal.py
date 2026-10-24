import json
import sqlite3
import sys

from conftest import make_detail

PWD = "motdepasse-solide"


def register(client, email="a@example.com", pwd=PWD, **extra):
    return client.post("/api/auth/register", json={"email": email, "password": pwd, **extra})


def test_routes_require_login(client):
    for url in ("/api/status", "/api/products", "/api/tickets", "/api/spending",
                "/api/tickets/T1", "/api/auth/me"):
        assert client.get(url).status_code == 401, url
    assert client.post("/api/sync").status_code == 401
    assert client.post("/api/lidl/start").status_code == 401
    assert client.post("/api/lidl/logout").status_code == 401
    assert client.delete("/api/account").status_code == 401


def test_register_login_logout(client):
    assert register(client).status_code == 200
    assert client.get("/api/auth/me").json() == {"email": "a@example.com", "demo": False}
    cookie = client.cookies.get("session")
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    # l'ancien cookie ne doit plus fonctionner côté serveur
    client.cookies.set("session", cookie)
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": "A@Example.com", "password": PWD})
    assert r.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_session_cookie_flags(client):
    set_cookie = register(client).headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie


def test_register_validation(client):
    assert register(client, pwd="court").status_code == 400
    assert register(client, email="pas-un-email").status_code == 400
    assert register(client).status_code == 200
    assert register(client, email="A@EXAMPLE.com").status_code == 409


def test_wrong_password_and_throttle(client):
    register(client)
    client.cookies.clear()
    for _ in range(8):
        r = client.post("/api/auth/login", json={"email": "a@example.com", "password": "mauvais"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"email": "a@example.com", "password": PWD})
    assert r.status_code == 429


def test_invite_code(env, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(env["config"], "REGISTRATION_CODE", "sesame")
    with TestClient(env["app"].app) as c:
        assert c.get("/api/config").json()["invite_required"] is True
        assert register(c).status_code == 403
        assert register(c, invite_code="faux").status_code == 403
        assert register(c, invite_code="sesame").status_code == 200


def test_data_isolation(client, env):
    db = env["db"]
    register(client, "a@example.com")
    uid_a = db.user_by_email("a@example.com")["id"]
    db.save_ticket(uid_a, {"id": "T1"}, make_detail("T1"))
    assert [t["id"] for t in client.get("/api/tickets").json()] == ["T1"]
    assert client.get("/api/tickets/T1").status_code == 200

    client.post("/api/auth/logout")
    register(client, "b@example.com")
    assert client.get("/api/tickets").json() == []
    assert client.get("/api/products").json() == []
    assert client.get("/api/spending").json() == []
    assert client.get("/api/tickets/T1").status_code == 404
    assert client.get("/api/history?key=123").json() == []
    assert client.get("/api/status").json()["stats"]["tickets"] == 0

    # même identifiant de ticket chez deux utilisateurs : pas de collision
    uid_b = db.user_by_email("b@example.com")["id"]
    db.save_ticket(uid_b, {"id": "T1"}, make_detail("T1", price="2,00"))
    assert client.get("/api/tickets/T1").json()["items"][0]["unit_price"] == 2.0
    assert db.known_ticket_ids(uid_a) == {"T1"}


def test_lidl_token_encrypted_per_user(env):
    db, accounts = env["db"], env["accounts"]
    from auth import TokenStore
    uid = db.create_user("a@example.com", "x")
    other = db.create_user("b@example.com", "x")
    TokenStore(uid).save({"access_token": "AT-secret", "refresh_token": "RT-secret",
                          "expires_in": 3600})
    raw = db.load_lidl_token(uid)
    assert "RT-secret" not in raw and "AT-secret" not in raw
    assert json.loads(accounts.decrypt(raw))["refresh_token"] == "RT-secret"
    assert TokenStore(uid).logged_in
    assert not TokenStore(other).logged_in
    TokenStore(uid).clear()
    assert not TokenStore(uid).logged_in


def test_delete_account_removes_everything(client, env):
    db = env["db"]
    from auth import TokenStore
    register(client)
    uid = db.user_by_email("a@example.com")["id"]
    db.save_ticket(uid, {"id": "T1"}, make_detail())
    TokenStore(uid).save({"access_token": "a", "refresh_token": "r"})
    assert client.delete("/api/account").status_code == 200
    with db.connect() as con:
        for table in ("users", "tickets", "items", "lidl_tokens", "sessions"):
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert client.get("/api/auth/me").status_code == 401


def test_migration_from_single_user_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LIDL_DATA_DIR", str(tmp_path))
    for name in ("config", "db"):
        sys.modules.pop(name, None)
    old = sqlite3.connect(tmp_path / "lidl.sqlite")
    old.executescript("""
        CREATE TABLE tickets (id TEXT PRIMARY KEY, date TEXT NOT NULL, store TEXT, total REAL,
                              raw_summary TEXT, raw_detail TEXT);
        CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT,
                            ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                            date TEXT NOT NULL, code TEXT, name TEXT NOT NULL, unit_price REAL,
                            quantity REAL, is_weight INTEGER, amount REAL, discount REAL);
        CREATE INDEX idx_items_code ON items(code);
        INSERT INTO tickets VALUES ('OLD1','2023-01-01T00:00:00','Lidl',5.0,'{}','{}');
        INSERT INTO items (ticket_id,date,code,name,unit_price,quantity,is_weight,amount,discount)
            VALUES ('OLD1','2023-01-01T00:00:00','9','Pain',1.0,1,0,1.0,0);
    """)
    old.commit()
    old.close()
    import db
    db.init()
    db.init()   # idempotent
    uid = db.create_user("a@example.com", "x")
    assert db.stats(uid)["tickets"] == 0
    assert db.adopt_legacy(uid) == (1, 1)
    assert db.stats(uid)["tickets"] == 1
    assert db.ticket_detail(uid, "OLD1")["items"][0]["name"] == "Pain"
