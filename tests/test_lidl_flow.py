import io
import time
import zipfile
from urllib.parse import parse_qs, urlparse

from test_portal import PWD, register


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


def start(client):
    r = client.post("/api/lidl/start")
    assert r.status_code == 200
    return r.json()


def test_start_requires_login(client):
    assert client.post("/api/lidl/start").status_code == 401


def test_start_builds_pkce_authorize_url(client):
    register(client)
    body = start(client)
    url = urlparse(body["authorize_url"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.lidl.com/connect/authorize"
    assert q["client_id"] == "LidlPlusNativeClient"
    assert q["redirect_uri"] == "com.lidlplus.app://callback"
    assert q["code_challenge_method"] == "S256" and q["code_challenge"]
    assert q["state"] == body["state"] and len(body["state"]) >= 16
    st = client.get("/api/status").json()
    assert st["login"]["status"] == "running" and not st["logged_in"]


def test_callback_exchanges_code_and_stores_token(client, env, monkeypatch):
    register(client)
    body = start(client)
    sent = {}

    def fake_post(url, headers=None, data=None, timeout=None):
        sent.update(url=url, data=data)
        return FakeResponse(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})

    monkeypatch.setattr("auth.requests.post", fake_post)
    client.cookies.clear()   # l'extension n'envoie aucun cookie : le state fait foi
    r = client.post("/api/lidl/callback", json={"code": "CODE123", "state": body["state"]})
    assert r.status_code == 200
    assert sent["url"] == "https://accounts.lidl.com/connect/token"
    assert sent["data"]["code"] == "CODE123" and sent["data"]["grant_type"] == "authorization_code"
    # le verifier envoyé correspond au challenge de l'URL d'autorisation
    import base64, hashlib
    challenge = parse_qs(urlparse(body["authorize_url"]).query)["code_challenge"][0]
    digest = hashlib.sha256(sent["data"]["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == challenge

    register_login = client.post("/api/auth/login", json={"email": "a@example.com", "password": PWD})
    assert register_login.status_code == 200
    st = client.get("/api/status").json()
    assert st["logged_in"] and st["login"]["status"] == "ok"


def test_callback_state_is_single_use_and_attributed_to_right_user(client, env, monkeypatch):
    db = env["db"]
    monkeypatch.setattr("auth.requests.post", lambda *a, **k: FakeResponse(
        200, {"access_token": "AT", "refresh_token": "RT"}))
    register(client, "a@example.com")
    state_a = start(client)["state"]
    client.post("/api/auth/logout")
    register(client, "b@example.com")
    state_b = start(client)["state"]

    assert client.post("/api/lidl/callback", json={"code": "c", "state": state_a}).status_code == 200
    assert db.load_lidl_token(db.user_by_email("a@example.com")["id"])
    assert db.load_lidl_token(db.user_by_email("b@example.com")["id"]) is None
    # rejeu du même state : refusé
    assert client.post("/api/lidl/callback", json={"code": "c", "state": state_a}).status_code == 400
    assert client.post("/api/lidl/callback", json={"code": "c", "state": state_b}).status_code == 200


def test_callback_rejects_unknown_expired_and_refused(client, env, monkeypatch):
    register(client)
    assert client.post("/api/lidl/callback", json={"code": "c", "state": "inconnu"}).status_code == 400

    body = start(client)
    monkeypatch.setattr("auth.requests.post", lambda *a, **k: FakeResponse(400, {"error": "x"}))
    r = client.post("/api/lidl/callback", json={"code": "c", "state": body["state"]})
    assert r.status_code == 400
    assert not client.get("/api/status").json()["logged_in"]

    body = start(client)
    real_time = time.time
    monkeypatch.setattr("auth.time.time", lambda: real_time() + 3600)
    assert client.post("/api/lidl/callback", json={"code": "c", "state": body["state"]}).status_code == 400


def test_new_start_invalidates_previous_request(client):
    register(client)
    first = start(client)["state"]
    start(client)
    assert client.post("/api/lidl/callback", json={"code": "c", "state": first}).status_code == 400


def test_stale_login_status_times_out(client, env, monkeypatch):
    register(client)
    start(client)
    real_time = time.time
    monkeypatch.setattr("app.time.time", lambda: real_time() + 3600)
    assert client.get("/api/status").json()["login"]["status"] == "error"


def test_extension_zip(client):
    r = client.get("/extension.zip")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "lidl-tickets-extension/manifest.json" in names
    assert "lidl-tickets-extension/background.js" in names
    assert not any(n.endswith(".test.js") for n in names)


def test_callback_network_error_is_not_a_500(client, monkeypatch):
    import requests
    register(client)
    body = start(client)

    def boom(*a, **k):
        raise requests.exceptions.SSLError("certificate verify failed")

    monkeypatch.setattr("auth.requests.post", boom)
    r = client.post("/api/lidl/callback", json={"code": "c", "state": body["state"]})
    assert r.status_code == 400 and "injoignable" in r.json()["detail"]
