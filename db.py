"""Stockage local SQLite des tickets et des lignes produit."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager

import config
from parser import parse_items, ticket_meta

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS lidl_tokens (
    user_id    INTEGER PRIMARY KEY,
    payload    TEXT NOT NULL,          -- JSON chiffré (Fernet)
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lidl_auth_requests (
    state_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    verifier   TEXT NOT NULL,          -- verifier PKCE chiffré (Fernet)
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    user_id     INTEGER NOT NULL,
    id          TEXT NOT NULL,
    date        TEXT NOT NULL,
    store       TEXT,
    total       REAL,
    raw_summary TEXT,
    raw_detail  TEXT,
    PRIMARY KEY (user_id, id)
);
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    ticket_id   TEXT NOT NULL,
    date        TEXT NOT NULL,
    code        TEXT,
    name        TEXT NOT NULL,
    unit_price  REAL,
    quantity    REAL,
    is_weight   INTEGER,
    amount      REAL,
    discount    REAL,
    FOREIGN KEY (user_id, ticket_id) REFERENCES tickets(user_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_items_user_code ON items(user_id, code);
CREATE INDEX IF NOT EXISTS idx_items_user_name ON items(user_id, name);
CREATE INDEX IF NOT EXISTS idx_items_user_date ON items(user_id, date);
-- Tables partagées entre utilisateurs : elles ne dépendent que de la référence produit
CREATE TABLE IF NOT EXISTS name_fixes (
    code       TEXT PRIMARY KEY,
    fixed_name TEXT NOT NULL,
    method     TEXT NOT NULL,
    fixed_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_labels (
    code       TEXT PRIMARY KEY,
    label      TEXT,
    brands     TEXT,
    off_code   TEXT,
    score      REAL,
    status     TEXT NOT NULL,
    matched_at TEXT NOT NULL
);
"""

LEGACY_USER_ID = 0   # tickets importés avant l'arrivée des comptes (voir adopt_legacy)


@contextmanager
def connect():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _migrate_single_user(con) -> None:
    """Ancienne base mono-utilisateur (sans colonne user_id) : on reconstruit
    tickets/items en rattachant les lignes existantes à LEGACY_USER_ID."""
    cols = [r["name"] for r in con.execute("PRAGMA table_info(tickets)")]
    if not cols or "user_id" in cols:
        return
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute("ALTER TABLE tickets RENAME TO tickets_old")
    con.execute("ALTER TABLE items RENAME TO items_old")
    con.execute("DROP INDEX IF EXISTS idx_items_code")
    con.execute("DROP INDEX IF EXISTS idx_items_name")
    con.execute("DROP INDEX IF EXISTS idx_items_date")
    con.executescript(SCHEMA)
    con.execute(
        """INSERT INTO tickets (user_id, id, date, store, total, raw_summary, raw_detail)
           SELECT ?, id, date, store, total, raw_summary, raw_detail FROM tickets_old""",
        (LEGACY_USER_ID,))
    con.execute(
        """INSERT INTO items (user_id, ticket_id, date, code, name, unit_price, quantity,
                              is_weight, amount, discount)
           SELECT ?, ticket_id, date, code, name, unit_price, quantity,
                  is_weight, amount, discount FROM items_old""", (LEGACY_USER_ID,))
    con.execute("DROP TABLE items_old")
    con.execute("DROP TABLE tickets_old")
    con.execute("PRAGMA foreign_keys = ON")


def init() -> None:
    with connect() as con:
        _migrate_single_user(con)
        con.executescript(SCHEMA)


# --------------------------------------------------------------------------- comptes
def create_user(email: str, password_hash: str) -> int:
    """Lève sqlite3.IntegrityError si l'e-mail existe déjà."""
    with connect() as con:
        cur = con.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?,?, datetime('now'))",
            (email, password_hash))
        return cur.lastrowid


def user_by_email(email: str) -> dict | None:
    with connect() as con:
        r = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(r) if r else None


def user_by_id(user_id: int) -> dict | None:
    with connect() as con:
        r = con.execute("SELECT id, email, created_at FROM users WHERE id = ?",
                        (user_id,)).fetchone()
        return dict(r) if r else None


def set_password_hash(user_id: int, password_hash: str) -> None:
    with connect() as con:
        con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))


def save_session(token_hash: str, user_id: int, expires_at: float) -> None:
    with connect() as con:
        con.execute("DELETE FROM sessions WHERE expires_at < CAST(strftime('%s','now') AS INTEGER)")
        con.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?,?,?)",
                    (token_hash, user_id, expires_at))


def user_for_session(token_hash: str, now: float) -> dict | None:
    with connect() as con:
        r = con.execute(
            """SELECT u.id, u.email FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ?""", (token_hash, now)).fetchone()
        return dict(r) if r else None


def delete_session(token_hash: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def delete_user(user_id: int) -> None:
    """Supprime le compte et TOUTES ses données (tickets bruts compris)."""
    with connect() as con:
        con.execute("DELETE FROM items WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM tickets WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM lidl_tokens WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM lidl_auth_requests WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))


def load_lidl_token(user_id: int) -> str | None:
    with connect() as con:
        r = con.execute("SELECT payload FROM lidl_tokens WHERE user_id = ?", (user_id,)).fetchone()
        return r[0] if r else None


def save_lidl_token(user_id: int, payload: str) -> None:
    with connect() as con:
        con.execute(
            """INSERT INTO lidl_tokens (user_id, payload, updated_at) VALUES (?,?, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                 payload=excluded.payload, updated_at=excluded.updated_at""",
            (user_id, payload))


def delete_lidl_token(user_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM lidl_tokens WHERE user_id = ?", (user_id,))


def save_auth_request(state_hash: str, user_id: int, verifier: str, expires_at: float) -> None:
    """Une seule demande en cours par utilisateur : la nouvelle remplace l'ancienne."""
    with connect() as con:
        con.execute("DELETE FROM lidl_auth_requests WHERE user_id = ? OR expires_at < CAST(strftime('%s','now') AS INTEGER)",
                    (user_id,))
        con.execute("INSERT INTO lidl_auth_requests (state_hash, user_id, verifier, expires_at) "
                    "VALUES (?,?,?,?)", (state_hash, user_id, verifier, expires_at))


def pop_auth_request(state_hash: str | None, now: float,
                     user_id: int | None = None) -> dict | None:
    """Lit puis supprime la demande (usage unique), si elle n'a pas expiré.

    Sélection par `state_hash`, et/ou par utilisateur (il n'a qu'une demande en cours).
    Avec `user_id`, la demande d'un autre utilisateur n'est ni lue ni supprimée."""
    where, params = [], []
    if state_hash is not None:
        where.append("state_hash = ?")
        params.append(state_hash)
    if user_id is not None:
        where.append("user_id = ?")
        params.append(user_id)
    if not where:
        return None
    clause = " AND ".join(where)
    with connect() as con:
        r = con.execute("SELECT state_hash, user_id, verifier, expires_at "
                        f"FROM lidl_auth_requests WHERE {clause}", params).fetchone()
        if r is None:
            return None
        con.execute("DELETE FROM lidl_auth_requests WHERE state_hash = ?", (r["state_hash"],))
        return dict(r) if r["expires_at"] > now else None


def purge_demo_users(domain: str, max_age_hours: int) -> int:
    """Supprime les comptes de démonstration plus vieux que la durée donnée."""
    with connect() as con:
        ids = [r[0] for r in con.execute(
            "SELECT id FROM users WHERE email LIKE ? AND created_at < datetime('now', ?)",
            (f"%@{domain}", f"-{int(max_age_hours)} hours"))]
    for user_id in ids:
        delete_user(user_id)
    return len(ids)


def export_user(user_id: int) -> dict:
    """Toutes les données d'un utilisateur (droit d'accès / portabilité). Le jeton Lidl,
    qui est un secret, n'en fait pas partie."""
    with connect() as con:
        user = con.execute("SELECT email, created_at FROM users WHERE id = ?",
                           (user_id,)).fetchone()
        tickets = [{"id": r["id"], "date": r["date"], "store": r["store"], "total": r["total"],
                    "summary": json.loads(r["raw_summary"] or "null"),
                    "detail": json.loads(r["raw_detail"] or "null")}
                   for r in con.execute(
                       "SELECT * FROM tickets WHERE user_id = ? ORDER BY date", (user_id,))]
        lidl = con.execute("SELECT updated_at FROM lidl_tokens WHERE user_id = ?",
                           (user_id,)).fetchone()
    return {"account": dict(user) if user else None,
            "lidl_linked_since": lidl[0] if lidl else None,
            "tickets": tickets}


def adopt_legacy(user_id: int) -> tuple[int, int]:
    """Rattache à ce compte les tickets importés avant l'arrivée des comptes."""
    # Connexion à part : la clé parente (user_id, id) change en même temps que celle
    # des lignes, ce qui exige de suspendre les clés étrangères le temps de la mise à jour.
    con = sqlite3.connect(config.DB_PATH)
    try:
        con.execute("PRAGMA foreign_keys = OFF")
        t = con.execute("UPDATE tickets SET user_id = ? WHERE user_id = ?",
                        (user_id, LEGACY_USER_ID)).rowcount
        i = con.execute("UPDATE items SET user_id = ? WHERE user_id = ?",
                        (user_id, LEGACY_USER_ID)).rowcount
        con.commit()
    finally:
        con.close()
    return t, i


# --------------------------------------------------------------------------- tickets
def known_ticket_ids(user_id: int) -> set[str]:
    with connect() as con:
        return {r[0] for r in con.execute(
            "SELECT id FROM tickets WHERE user_id = ?", (user_id,))}


def codes_needing_enrichment(force: bool = False) -> list[tuple[str, str]]:
    """Une (référence, dernier libellé brut vu) par référence produit connue."""
    with connect() as con:
        rows = con.execute(
            """SELECT code, name FROM (
                   SELECT code, name,
                          ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
                   FROM items WHERE code != ''
               ) WHERE rn = 1 ORDER BY code"""
        ).fetchall()
        if force:
            return [(r["code"], r["name"]) for r in rows]
        done = {r[0] for r in con.execute("SELECT code FROM product_labels")}
        return [(r["code"], r["name"]) for r in rows if r["code"] not in done]


def save_label(code: str, label: str | None, brands: str | None, off_code: str | None,
               score: float | None, status: str) -> None:
    with connect() as con:
        con.execute(
            """INSERT INTO product_labels (code, label, brands, off_code, score, status, matched_at)
               VALUES (?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(code) DO UPDATE SET
                 label=excluded.label, brands=excluded.brands, off_code=excluded.off_code,
                 score=excluded.score, status=excluded.status, matched_at=excluded.matched_at""",
            (code, label, brands, off_code, score, status))


def label_stats() -> dict:
    with connect() as con:
        r = con.execute(
            "SELECT COUNT(*), SUM(status='matched') FROM product_labels").fetchone()
    return {"codes_traites": r[0] or 0, "libelles_trouves": r[1] or 0}


def codes_with_corrupted_name() -> list[tuple[str, str]]:
    """Une (référence, dernier nom brut vu) par référence dont le nom contient
    le caractère de remplacement Unicode �."""
    return [(code, name) for code, name in codes_needing_enrichment(force=True) if "�" in name]


def save_name_fix(code: str, fixed_name: str, method: str) -> None:
    with connect() as con:
        con.execute(
            """INSERT INTO name_fixes (code, fixed_name, method, fixed_at)
               VALUES (?,?,?, datetime('now'))
               ON CONFLICT(code) DO UPDATE SET
                 fixed_name=excluded.fixed_name, method=excluded.method, fixed_at=excluded.fixed_at""",
            (code, fixed_name, method))


def name_fix_stats() -> dict:
    with connect() as con:
        r = con.execute(
            "SELECT COUNT(*), SUM(method='guess') FROM name_fixes").fetchone()
    return {"total": r[0] or 0, "devines": r[1] or 0}


def _insert_items(con, user_id: int, ticket_id: str, date: str, detail: dict) -> int:
    con.execute("DELETE FROM items WHERE user_id = ? AND ticket_id = ?", (user_id, ticket_id))
    items = parse_items(detail)
    con.executemany(
        """INSERT INTO items (user_id, ticket_id, date, code, name, unit_price, quantity,
                              is_weight, amount, discount)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [(user_id, ticket_id, date, i["code"], i["name"], i["unit_price"], i["quantity"],
          i["is_weight"], i["amount"], i["discount"]) for i in items])
    return len(items)


def _save_ticket(con, user_id: int, summary: dict, detail: dict) -> int:
    meta = ticket_meta(summary, detail)
    con.execute(
        """INSERT OR REPLACE INTO tickets (user_id, id, date, store, total, raw_summary, raw_detail)
           VALUES (?,?,?,?,?,?,?)""",
        (user_id, meta["id"], meta["date"], meta["store"], meta["total"],
         json.dumps(summary, ensure_ascii=False), json.dumps(detail, ensure_ascii=False)))
    return _insert_items(con, user_id, meta["id"], meta["date"], detail)


def save_ticket(user_id: int, summary: dict, detail: dict) -> int:
    with connect() as con:
        return _save_ticket(con, user_id, summary, detail)


def save_tickets(user_id: int, tickets: list[tuple[dict, dict]]) -> int:
    """Plusieurs tickets (résumé, détail) en une seule transaction."""
    with connect() as con:
        return sum(_save_ticket(con, user_id, s, d) for s, d in tickets)


def reparse_all() -> tuple[int, int]:
    """Ré-analyse les tickets bruts de tous les utilisateurs (après modification de parser.py)."""
    n_tickets = n_items = 0
    with connect() as con:
        rows = con.execute("SELECT user_id, id, date, raw_detail FROM tickets").fetchall()
        for r in rows:
            n_items += _insert_items(con, r["user_id"], r["id"], r["date"], json.loads(r["raw_detail"]))
            n_tickets += 1
    return n_tickets, n_items


# --------------------------------------------------------------------------- lectures
def stats(user_id: int) -> dict:
    with connect() as con:
        t = con.execute("SELECT COUNT(*), MIN(date), MAX(date), SUM(total) FROM tickets "
                        "WHERE user_id = ?", (user_id,)).fetchone()
        n_items = con.execute("SELECT COUNT(*) FROM items WHERE user_id = ?",
                              (user_id,)).fetchone()[0]
    return {"tickets": t[0], "first_date": t[1], "last_date": t[2],
            "total_spent": round(t[3] or 0, 2), "items": n_items}


def _key_expr(group: str) -> str:
    return "name" if group == "name" else "COALESCE(NULLIF(code, ''), name)"


_SORT_KEYS = {
    "purchases": lambda x: x["purchases"],
    "price": lambda x: x["last_price"] if x["last_price"] is not None else -1,
    "increase": lambda x: x["change_pct"] if x["change_pct"] is not None else -float("inf"),
}


def products(user_id: int, query: str = "", group: str = "code",
             sort: str = "purchases") -> list[dict]:
    key = _key_expr(group)
    sql = f"SELECT {key} AS k, name, date, unit_price, is_weight FROM items WHERE user_id = ?"
    params: list = [user_id]
    if query:
        sql += " AND (name LIKE ? OR code LIKE ?)"
        params += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY date"
    agg: dict[str, dict] = defaultdict(lambda: {"n": 0, "prices": []})
    with connect() as con:
        for r in con.execute(sql, params):
            a = agg[r["k"]]
            a["key"] = r["k"]
            a["name"] = r["name"]           # nom le plus récent
            a["is_weight"] = r["is_weight"]
            a["n"] += 1
            a["prices"].append((r["date"], r["unit_price"]))
        labels = fixes = {}
        if group == "code":
            labels = {r["code"]: r["label"] for r in con.execute(
                "SELECT code, label FROM product_labels WHERE status='matched'")}
            fixes = {r["code"]: r["fixed_name"] for r in con.execute(
                "SELECT code, fixed_name FROM name_fixes")}
    out = []
    for a in agg.values():
        prices = [p for _, p in a["prices"] if p is not None]
        first, last = a["prices"][0], a["prices"][-1]
        # une correction devinée ne sert que tant que le nom brut est encore abîmé
        guessed = fixes.get(a["key"]) if "�" in a["name"] else None
        display_name = labels.get(a["key"]) or guessed or a["name"]
        out.append({
            "key": a["key"], "name": display_name, "is_weight": a["is_weight"],
            "purchases": a["n"],
            "first_date": first[0][:10], "first_price": first[1],
            "last_date": last[0][:10], "last_price": last[1],
            "min_price": min(prices), "max_price": max(prices),
            "change_pct": round((last[1] - first[1]) / first[1] * 100, 1) if first[1] else None,
        })
    out.sort(key=_SORT_KEYS.get(sort, _SORT_KEYS["purchases"]), reverse=True)
    return out


def product_history(user_id: int, key: str, group: str = "code") -> list[dict]:
    sql = f"""SELECT i.date, COALESCE(pl.label, CASE WHEN instr(i.name, char(65533)) > 0 THEN nf.fixed_name END, i.name) AS name, i.code,
                     i.unit_price, i.quantity, i.is_weight, i.amount, i.discount,
                     i.ticket_id, t.store
              FROM items i JOIN tickets t ON t.user_id = i.user_id AND t.id = i.ticket_id
              LEFT JOIN product_labels pl ON pl.code = i.code AND pl.status = 'matched'
              LEFT JOIN name_fixes nf ON nf.code = i.code
              WHERE i.user_id = ?
                AND {_key_expr(group).replace('name', 'i.name').replace('code', 'i.code')} = ?
              ORDER BY i.date"""
    with connect() as con:
        return [dict(r) for r in con.execute(sql, (user_id, key))]


def tickets(user_id: int, limit: int = 500) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            """SELECT t.id, t.date, t.store, t.total, COUNT(i.id) AS n_items
               FROM tickets t LEFT JOIN items i ON i.user_id = t.user_id AND i.ticket_id = t.id
               WHERE t.user_id = ?
               GROUP BY t.id ORDER BY t.date DESC LIMIT ?""", (user_id, limit))]


def ticket_detail(user_id: int, ticket_id: str) -> dict | None:
    """Lignes d'un ticket, enrichies du prix le plus ancien connu pour chaque
    référence (toute l'historique confondu), et du total reconstitué si
    chaque article avait été payé à ce prix le plus ancien."""
    with connect() as con:
        ticket = con.execute(
            "SELECT id, date, store, total FROM tickets WHERE user_id = ? AND id = ?",
            (user_id, ticket_id)).fetchone()
        if ticket is None:
            return None
        items = [dict(r) for r in con.execute(
            """SELECT i.code, COALESCE(pl.label, CASE WHEN instr(i.name, char(65533)) > 0 THEN nf.fixed_name END, i.name) AS name, i.unit_price,
                      i.quantity, i.is_weight, i.amount, i.discount,
                      COALESCE(NULLIF(i.code, ''), i.name) AS _key
               FROM items i
               LEFT JOIN product_labels pl ON pl.code = i.code AND pl.status = 'matched'
               LEFT JOIN name_fixes nf ON nf.code = i.code
               WHERE i.user_id = ? AND i.ticket_id = ? ORDER BY i.id""",
            (user_id, ticket_id))]

        keys = {it["_key"] for it in items}
        first_by_key: dict[str, tuple[str, float]] = {}
        if keys:
            placeholders = ",".join("?" * len(keys))
            rows = con.execute(
                f"""SELECT k, date, unit_price FROM (
                        SELECT COALESCE(NULLIF(code, ''), name) AS k, date, unit_price,
                               ROW_NUMBER() OVER (PARTITION BY COALESCE(NULLIF(code, ''), name)
                                                   ORDER BY date ASC) AS rn
                        FROM items
                        WHERE user_id = ?
                          AND COALESCE(NULLIF(code, ''), name) IN ({placeholders})
                    ) WHERE rn = 1""", (user_id, *keys)).fetchall()
            first_by_key = {r["k"]: (r["date"], r["unit_price"]) for r in rows}

    total_first = 0.0
    for it in items:
        first_date, first_price = first_by_key.get(it["_key"], (None, None))
        it["first_date"] = first_date[:10] if first_date else None
        it["first_price"] = first_price
        it["first_is_now"] = first_date is not None and first_date == ticket["date"]
        del it["_key"]
        if first_price is not None:
            total_first += first_price * (it["quantity"] or 0)

    total_first = round(total_first, 2)
    return {
        "id": ticket["id"], "date": ticket["date"], "store": ticket["store"],
        "total": ticket["total"], "items": items,
        "total_at_first_price": total_first,
        "delta_vs_first_price": round((ticket["total"] or 0) - total_first, 2),
    }


def monthly_spending(user_id: int) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            """SELECT substr(date, 1, 7) AS month, ROUND(SUM(total), 2) AS total,
                      COUNT(*) AS tickets
               FROM tickets WHERE user_id = ? GROUP BY month ORDER BY month""",
            (user_id,))]
