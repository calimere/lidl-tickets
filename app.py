"""Serveur web FastAPI : API JSON + interface (static/index.html).

Lancement :  python app.py   puis ouvrir http://127.0.0.1:8000
"""
from __future__ import annotations

import threading
import time
import traceback

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
import db
from auth import AuthError, TokenStore, interactive_login
from lidl_client import LidlClient

app = FastAPI(title="Suivi tickets Lidl")
app.mount("/static", StaticFiles(directory=config.BASE_DIR / "static"), name="static")

db.init()
store = TokenStore()
state = {
    "login": {"status": "idle", "message": ""},
    "sync": {"running": False, "done": 0, "total": 0, "message": "", "error": None},
}
lock = threading.Lock()


# --------------------------------------------------------------------------- tâches de fond
def _login_job():
    state["login"] = {"status": "running",
                      "message": "Connectez-vous dans la fenêtre Chromium qui vient de s'ouvrir."}
    try:
        interactive_login(store)
        state["login"] = {"status": "ok", "message": "Connecté."}
    except Exception as e:  # noqa: BLE001
        state["login"] = {"status": "error", "message": str(e)}


def _sync_job():
    s = state["sync"]
    s.update(running=True, done=0, total=0, error=None, message="Liste des tickets…")
    try:
        client = LidlClient(store)
        known = db.known_ticket_ids()
        summaries = list(client.iter_ticket_summaries())
        new = [t for t in summaries if t.get("id") not in known]
        s.update(total=len(new),
                 message=f"{len(summaries)} tickets sur le compte, {len(new)} nouveaux.")
        for i, summary in enumerate(new, 1):
            detail = client.ticket(summary["id"])
            db.save_ticket(summary, detail)
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


# --------------------------------------------------------------------------- routes
@app.get("/")
def index():
    return FileResponse(config.BASE_DIR / "static" / "index.html")


@app.get("/api/status")
def status():
    return {"logged_in": store.logged_in, "country": config.COUNTRY,
            "login": state["login"], "sync": state["sync"], "stats": db.stats()}


@app.post("/api/login")
def login():
    with lock:
        if state["login"]["status"] == "running":
            raise HTTPException(409, "Connexion déjà en cours.")
        threading.Thread(target=_login_job, daemon=True).start()
    return {"ok": True}


@app.post("/api/logout")
def logout():
    store.clear()
    state["login"] = {"status": "idle", "message": "Déconnecté."}
    return {"ok": True}


@app.post("/api/sync")
def sync():
    if not store.logged_in:
        raise HTTPException(401, "Connectez-vous d'abord.")
    with lock:
        if state["sync"]["running"]:
            raise HTTPException(409, "Synchronisation déjà en cours.")
        threading.Thread(target=_sync_job, daemon=True).start()
    return {"ok": True}


@app.get("/api/products")
def products(q: str = "", group: str = "code", sort: str = "purchases"):
    return db.products(q, group, sort)


@app.get("/api/history")
def history(key: str, group: str = "code"):
    return db.product_history(key, group)


@app.get("/api/tickets")
def tickets():
    return db.tickets()


@app.get("/api/tickets/{ticket_id}")
def ticket(ticket_id: str):
    detail = db.ticket_detail(ticket_id)
    if detail is None:
        raise HTTPException(404, "Ticket introuvable.")
    return detail


@app.get("/api/spending")
def spending():
    return db.monthly_spending()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=3006)
