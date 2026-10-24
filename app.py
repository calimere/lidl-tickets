"""Serveur web FastAPI : API JSON + interface (static/index.html).

Lancement :  python app.py   puis ouvrir http://127.0.0.1:3006
"""
from __future__ import annotations

import threading
import time
import traceback

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import accounts
import config
import db
from accounts import AccountError
from auth import AuthError, TokenStore, interactive_login
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
def _login_job(user_id: int):
    state = state_of(user_id)
    state["login"] = {"status": "running",
                      "message": "Connectez-vous dans la fenêtre Chromium qui vient de s'ouvrir."}
    try:
        interactive_login(TokenStore(user_id))
        state["login"] = {"status": "ok", "message": "Connecté."}
    except Exception as e:  # noqa: BLE001
        state["login"] = {"status": "error", "message": str(e)}


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


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return {"email": user["email"]}


@app.delete("/api/account")
def delete_account(response: Response, user: dict = Depends(current_user)):
    """Supprime le compte et toutes ses données (droit à l'effacement)."""
    db.delete_user(user["id"])
    states.pop(user["id"], None)
    response.delete_cookie(accounts.COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/config")
def public_config():
    """Informations affichées sur l'écran de connexion (non authentifié)."""
    return {"invite_required": bool(config.REGISTRATION_CODE)}


# --------------------------------------------------------------------------- routes
@app.get("/")
def index():
    return FileResponse(config.BASE_DIR / "static" / "index.html")


@app.get("/api/status")
def status(user: dict = Depends(current_user)):
    state = state_of(user["id"])
    return {"logged_in": TokenStore(user["id"]).logged_in, "country": config.COUNTRY,
            "email": user["email"], "login": state["login"], "sync": state["sync"],
            "stats": db.stats(user["id"])}


@app.post("/api/lidl/login")
def lidl_login(user: dict = Depends(current_user)):
    state = state_of(user["id"])
    with lock:
        if state["login"]["status"] == "running":
            raise HTTPException(409, "Connexion déjà en cours.")
        state["login"] = {"status": "running", "message": ""}
    threading.Thread(target=_login_job, args=(user["id"],), daemon=True).start()
    return {"ok": True}


@app.post("/api/lidl/logout")
def lidl_logout(user: dict = Depends(current_user)):
    TokenStore(user["id"]).clear()
    state_of(user["id"])["login"] = {"status": "idle", "message": "Déconnecté."}
    return {"ok": True}


@app.post("/api/sync")
def sync(user: dict = Depends(current_user)):
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
    uvicorn.run(app, host="127.0.0.1", port=3006)
