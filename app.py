"""Serveur web FastAPI : API JSON + interface (static/index.html).

Lancement :  python app.py   puis ouvrir http://127.0.0.1:3006
"""
from __future__ import annotations

import io
import os
import threading
import time
import traceback
import zipfile

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response as RawResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import accounts
import config
import db
import demo
from accounts import AccountError
from auth import (AUTH_REQUEST_TTL, AuthError, TokenStore, complete_authorization,
                  parse_pasted_callback, start_authorization)
from lidl_client import LidlClient

app = FastAPI(title="Suivi tickets Lidl")
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "static"), name="static")

db.init()
# État des tâches de fond, par utilisateur
states: dict[int, dict] = {}
lock = threading.Lock()


def state_of(user_id: int) -> dict:
    with lock:
        return states.setdefault(user_id, {
            "login": {"status": "idle", "message": ""},
            "sync": {"running": False, "done": 0, "total": 0, "message": "", "error": None},
        })


# --------------------------------------------------------------------------- tâches de fond
def _sync_job(user_id: int):
    s = state_of(user_id)["sync"]
    s.update(running=True, done=0, total=0, error=None, message="Liste des tickets…")
    try:
        client = LidlClient(TokenStore(user_id))
        known = db.known_ticket_ids(user_id)
        summaries = list(client.iter_ticket_summaries())
        new = [t for t in summaries if t.get("id") not in known]
        s.update(total=len(new),
                 message=f"{len(summaries)} tickets sur le compte, {len(new)} nouveaux.")
        for i, summary in enumerate(new, 1):
            detail = client.ticket(summary["id"])
            db.save_ticket(user_id, summary, detail)
            s["done"] = i
            time.sleep(config.REQUEST_DELAY)
        s["message"] = f"Terminé : {len(new)} nouveaux tickets importés."
    except AuthError as e:
        s.update(error=str(e), message="Erreur d'authentification.")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        s.update(error=f"{type(e).__name__}: {e}", message="Erreur pendant la synchronisation.")
    finally:
        s["running"] = False


# --------------------------------------------------------------------------- comptes du portail
class Credentials(BaseModel):
    email: str
    password: str
    invite_code: str = ""


def current_user(request: Request) -> dict:
    user = accounts.user_from_token(request.cookies.get(accounts.COOKIE_NAME))
    if user is None:
        raise HTTPException(401, "Non connecté.")
    return user


def _start_session(response: Response, user_id: int) -> None:
    response.set_cookie(accounts.COOKIE_NAME, accounts.create_session(user_id),
                        max_age=config.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=config.COOKIE_SECURE, path="/")


@app.exception_handler(AccountError)
def account_error_handler(_: Request, e: AccountError):
    return JSONResponse({"detail": str(e)}, status_code=e.status)


@app.post("/api/auth/register")
def register(body: Credentials, response: Response):
    user_id = accounts.register(body.email, body.password, body.invite_code)
    _start_session(response, user_id)
    return {"ok": True}


@app.post("/api/auth/login")
def login_portal(body: Credentials, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    user_id = accounts.authenticate(body.email, body.password,
                                    f"{ip}|{body.email.strip().lower()}")
    _start_session(response, user_id)
    return {"ok": True}


@app.post("/api/auth/logout")
def logout_portal(request: Request, response: Response):
    accounts.end_session(request.cookies.get(accounts.COOKIE_NAME))
    response.delete_cookie(accounts.COOKIE_NAME, path="/")
    return {"ok": True}


@app.post("/api/auth/demo")
def start_demo(request: Request, response: Response):
    """Ouvre un compte jetable avec des tickets fictifs (aucun compte Lidl requis)."""
    ip = request.client.host if request.client else "?"
    _start_session(response, accounts.create_demo_user(f"demo|{ip}"))
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return {"email": user["email"], "demo": demo.is_demo_email(user["email"])}


def real_user(user: dict = Depends(current_user)) -> dict:
    """Comme current_user, mais refuse les comptes de démonstration."""
    if demo.is_demo_email(user["email"]):
        raise HTTPException(403, "Indisponible en mode démo : créez un compte pour relier Lidl Plus.")
    return user


@app.delete("/api/account")
def delete_account(response: Response, user: dict = Depends(current_user)):
    """Supprime le compte et toutes ses données (droit à l'effacement)."""
    db.delete_user(user["id"])
    states.pop(user["id"], None)
    response.delete_cookie(accounts.COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/account/export")
def export_account(user: dict = Depends(current_user)):
    """Export JSON de toutes les données du compte (droit d'accès et portabilité)."""
    return JSONResponse(db.export_user(user["id"]), headers={
        "Content-Disposition": 'attachment; filename="mes-donnees-lidl-tickets.json"'})


@app.get("/confidentialite", response_class=HTMLResponse)
def privacy():
    import html
    page = (config.BASE_DIR / "static" / "privacy.html").read_text(encoding="utf-8")
    for key, value in (("PUBLISHER", config.PUBLISHER), ("CONTACT", config.CONTACT_EMAIL),
                       ("TTL_DEMO", str(config.DEMO_TTL_HOURS)),
                       ("SESSION_DAYS", str(config.SESSION_DAYS))):
        page = page.replace("{{%s}}" % key, html.escape(value) or "<em>(non renseigné)</em>")
    return page


@app.get("/api/config")
def public_config():
    """Informations affichées sur l'écran de connexion (non authentifié)."""
    return {"invite_required": bool(config.REGISTRATION_CODE),
            "firefox_xpi": (config.BASE_DIR / "extension" / "dist" / "lidl-tickets.xpi").is_file()}


# --------------------------------------------------------------------------- routes
@app.get("/")
def index():
    return FileResponse(config.BASE_DIR / "static" / "index.html")


@app.get("/api/status")
def status(user: dict = Depends(current_user)):
    state = state_of(user["id"])
    login = state["login"]
    if login["status"] == "running" and time.time() > login.get("expires_at", 0):
        state["login"] = {"status": "error",
                          "message": "Délai dépassé : relancez la connexion à Lidl Plus."}
    return {"logged_in": TokenStore(user["id"]).logged_in, "country": config.COUNTRY,
            "email": user["email"], "demo": demo.is_demo_email(user["email"]),
            "login": state["login"], "sync": state["sync"],
            "stats": db.stats(user["id"])}


@app.post("/api/lidl/start")
def lidl_start(user: dict = Depends(real_user)):
    """Prépare la connexion Lidl : l'extension ouvre l'URL et renvoie le code à /callback."""
    url, state = start_authorization(user["id"])
    state_of(user["id"])["login"] = {
        "status": "running", "expires_at": time.time() + AUTH_REQUEST_TTL,
        "message": "Connectez-vous à Lidl Plus dans l'onglet qui vient de s'ouvrir."}
    return {"authorize_url": url, "state": state}


class LidlCallback(BaseModel):
    code: str
    state: str


@app.post("/api/lidl/callback")
def lidl_callback(body: LidlCallback):
    """Appelé par l'extension Chrome, sans cookie : le `state` à usage unique fait foi."""
    try:
        user_id = complete_authorization(body.state, body.code)
    except AuthError as e:
        raise HTTPException(400, str(e)) from None
    state_of(user_id)["login"] = {"status": "ok", "message": "Connecté."}
    return {"ok": True}


def build_extension_zip(browser: str) -> bytes:
    """Zip de l'extension pour `chrome` ou `firefox` (seul le manifest diffère)."""
    root = config.BASE_DIR / "extension"
    manifest = root / ("manifest.firefox.json" if browser == "firefox" else "manifest.json")
    skip = {"manifest.json", "manifest.firefox.json"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(manifest, "lidl-tickets-extension/manifest.json")
        for f in sorted(root.rglob("*")):
            if f.is_file() and f.name not in skip and not f.name.endswith(".test.js"):
                z.write(f, f"lidl-tickets-extension/{f.relative_to(root).as_posix()}")
    return buf.getvalue()


class PastedLink(BaseModel):
    url: str


@app.post("/api/lidl/paste")
def lidl_paste(body: PastedLink, user: dict = Depends(real_user)):
    """Connexion manuelle (sans extension) : l'utilisateur colle le lien de redirection
    `com.lidlplus.app://callback?code=…` copié depuis les outils de développement."""
    try:
        code, state = parse_pasted_callback(body.url)
        complete_authorization(state, code, user_id=user["id"])
    except AuthError as e:
        raise HTTPException(400, str(e)) from None
    state_of(user["id"])["login"] = {"status": "ok", "message": "Connecté."}
    return {"ok": True}


@app.get("/extension.zip")
def extension_zip(browser: str = "chrome"):
    """L'extension, à décompresser puis charger (chrome://extensions ou about:debugging)."""
    if browser not in ("chrome", "firefox"):
        raise HTTPException(400, "Navigateur inconnu.")
    return RawResponse(build_extension_zip(browser), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="lidl-tickets-extension-{browser}.zip"'})


@app.get("/extension.xpi")
def extension_xpi():
    """Extension Firefox signée par Mozilla (AMO, mode « non répertorié »), si fournie."""
    xpi = config.BASE_DIR / "extension" / "dist" / "lidl-tickets.xpi"
    if not xpi.is_file():
        raise HTTPException(404, "Aucune extension Firefox signée n'est installée sur ce serveur.")
    return FileResponse(xpi, media_type="application/x-xpinstall",
                        filename="lidl-tickets.xpi")


@app.post("/api/lidl/logout")
def lidl_logout(user: dict = Depends(current_user)):
    TokenStore(user["id"]).clear()
    state_of(user["id"])["login"] = {"status": "idle", "message": "Déconnecté."}
    return {"ok": True}


@app.post("/api/sync")
def sync(user: dict = Depends(real_user)):
    if not TokenStore(user["id"]).logged_in:
        raise HTTPException(401, "Connectez-vous d'abord à Lidl Plus.")
    state = state_of(user["id"])
    with lock:
        if state["sync"]["running"]:
            raise HTTPException(409, "Synchronisation déjà en cours.")
        state["sync"]["running"] = True
    threading.Thread(target=_sync_job, args=(user["id"],), daemon=True).start()
    return {"ok": True}


@app.get("/api/products")
def products(q: str = "", group: str = "code", sort: str = "purchases",
             user: dict = Depends(current_user)):
    return db.products(user["id"], q, group, sort)


@app.get("/api/history")
def history(key: str, group: str = "code", user: dict = Depends(current_user)):
    return db.product_history(user["id"], key, group)


@app.get("/api/tickets")
def tickets(user: dict = Depends(current_user)):
    return db.tickets(user["id"])


@app.get("/api/tickets/{ticket_id}")
def ticket(ticket_id: str, user: dict = Depends(current_user)):
    detail = db.ticket_detail(user["id"], ticket_id)
    if detail is None:
        raise HTTPException(404, "Ticket introuvable.")
    return detail


@app.get("/api/spending")
def spending(user: dict = Depends(current_user)):
    return db.monthly_spending(user["id"])


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("LIDL_HOST", "127.0.0.1"), port=3006)
