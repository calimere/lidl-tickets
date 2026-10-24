from conftest import make_detail
from test_portal import register


def test_demo_account_has_fake_data_and_is_flagged(client):
    assert client.post("/api/auth/demo").status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["demo"] is True and me["email"].endswith("@demo.invalid")
    st = client.get("/api/status").json()
    assert st["demo"] is True and st["stats"]["tickets"] > 100
    assert client.get("/api/products").json()
    assert client.get("/api/spending").json()


def test_demo_cannot_link_lidl_or_sync(client):
    client.post("/api/auth/demo")
    assert client.post("/api/lidl/start").status_code == 403
    assert client.post("/api/sync").status_code == 403


def test_demo_creation_is_throttled(client):
    codes = [client.post("/api/auth/demo").status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_demo_accounts_are_purged_after_ttl(client, env):
    db = env["db"]
    client.post("/api/auth/demo")
    register(client, "keep@example.com")
    with db.connect() as con:
        con.execute("UPDATE users SET created_at = datetime('now', '-25 hours')")
    assert db.purge_demo_users("demo.invalid", 24) == 1
    with db.connect() as con:
        assert [r[0] for r in con.execute("SELECT email FROM users")] == ["keep@example.com"]
        assert con.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0


def test_demo_account_can_be_deleted_by_its_owner(client):
    client.post("/api/auth/demo")
    assert client.delete("/api/account").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_export_contains_own_data_only_and_no_secret(client, env):
    db = env["db"]
    from auth import TokenStore
    register(client, "a@example.com")
    uid_a = db.user_by_email("a@example.com")["id"]
    db.save_ticket(uid_a, {"id": "T1"}, make_detail("T1"))
    TokenStore(uid_a).save({"access_token": "AT-secret", "refresh_token": "RT-secret"})
    client.post("/api/auth/logout")
    register(client, "b@example.com")
    uid_b = db.user_by_email("b@example.com")["id"]
    db.save_ticket(uid_b, {"id": "T2"}, make_detail("T2"))

    r = client.get("/api/account/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["account"]["email"] == "b@example.com"
    assert [t["id"] for t in data["tickets"]] == ["T2"]
    assert data["tickets"][0]["detail"]["itemsLine"][0]["description"] == "Lait"
    assert "secret" not in r.text and "password" not in r.text
    client.cookies.clear()
    assert client.get("/api/account/export").status_code == 401


def test_privacy_page_is_public_and_filled_from_config(client, env, monkeypatch):
    monkeypatch.setattr(env["config"], "PUBLISHER", "Jane <Doe> EI")
    monkeypatch.setattr(env["config"], "CONTACT_EMAIL", "privacy@example.com")
    r = client.get("/confidentialite")
    assert r.status_code == 200
    assert "Jane &lt;Doe&gt; EI" in r.text and "privacy@example.com" in r.text
    assert "{{" not in r.text


def test_privacy_page_flags_missing_publisher(client):
    assert "non renseigné" in client.get("/confidentialite").text
