"""Stockage local SQLite des tickets et des lignes produit."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager

import config
from parser import parse_items, ticket_meta

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          TEXT PRIMARY KEY,
    date        TEXT NOT NULL,
    store       TEXT,
    total       REAL,
    raw_summary TEXT,
    raw_detail  TEXT
);
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    code        TEXT,
    name        TEXT NOT NULL,
    unit_price  REAL,
    quantity    REAL,
    is_weight   INTEGER,
    amount      REAL,
    discount    REAL
);
CREATE INDEX IF NOT EXISTS idx_items_code ON items(code);
CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
CREATE INDEX IF NOT EXISTS idx_items_date ON items(date);
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


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)


def known_ticket_ids() -> set[str]:
    with connect() as con:
        return {r[0] for r in con.execute("SELECT id FROM tickets")}


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


def _insert_items(con, ticket_id: str, date: str, detail: dict) -> int:
    con.execute("DELETE FROM items WHERE ticket_id = ?", (ticket_id,))
    items = parse_items(detail)
    con.executemany(
        """INSERT INTO items (ticket_id, date, code, name, unit_price, quantity,
                              is_weight, amount, discount)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [(ticket_id, date, i["code"], i["name"], i["unit_price"], i["quantity"],
          i["is_weight"], i["amount"], i["discount"]) for i in items])
    return len(items)


def save_ticket(summary: dict, detail: dict) -> int:
    meta = ticket_meta(summary, detail)
    with connect() as con:
        con.execute(
            """INSERT OR REPLACE INTO tickets (id, date, store, total, raw_summary, raw_detail)
               VALUES (?,?,?,?,?,?)""",
            (meta["id"], meta["date"], meta["store"], meta["total"],
             json.dumps(summary, ensure_ascii=False), json.dumps(detail, ensure_ascii=False)))
        return _insert_items(con, meta["id"], meta["date"], detail)


def reparse_all() -> tuple[int, int]:
    """Ré-analyse tous les tickets bruts stockés (après modification de parser.py)."""
    n_tickets = n_items = 0
    with connect() as con:
        rows = con.execute("SELECT id, date, raw_detail FROM tickets").fetchall()
        for r in rows:
            n_items += _insert_items(con, r["id"], r["date"], json.loads(r["raw_detail"]))
            n_tickets += 1
    return n_tickets, n_items


# --------------------------------------------------------------------------- lectures
def stats() -> dict:
    with connect() as con:
        t = con.execute("SELECT COUNT(*), MIN(date), MAX(date), SUM(total) FROM tickets").fetchone()
        n_items = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    return {"tickets": t[0], "first_date": t[1], "last_date": t[2],
            "total_spent": round(t[3] or 0, 2), "items": n_items}


def _key_expr(group: str) -> str:
    return "name" if group == "name" else "COALESCE(NULLIF(code, ''), name)"


_SORT_KEYS = {
    "purchases": lambda x: x["purchases"],
    "price": lambda x: x["last_price"] if x["last_price"] is not None else -1,
    "increase": lambda x: x["change_pct"] if x["change_pct"] is not None else -float("inf"),
}


def products(query: str = "", group: str = "code", sort: str = "purchases") -> list[dict]:
    key = _key_expr(group)
    sql = f"SELECT {key} AS k, name, date, unit_price, is_weight FROM items"
    params: list = []
    if query:
        sql += " WHERE name LIKE ? OR code LIKE ?"
        params = [f"%{query}%", f"%{query}%"]
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
        display_name = labels.get(a["key"]) or fixes.get(a["key"]) or a["name"]
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


def product_history(key: str, group: str = "code") -> list[dict]:
    sql = f"""SELECT i.date, COALESCE(pl.label, nf.fixed_name, i.name) AS name, i.code,
                     i.unit_price, i.quantity, i.is_weight, i.amount, i.discount,
                     i.ticket_id, t.store
              FROM items i JOIN tickets t ON t.id = i.ticket_id
              LEFT JOIN product_labels pl ON pl.code = i.code AND pl.status = 'matched'
              LEFT JOIN name_fixes nf ON nf.code = i.code
              WHERE {_key_expr(group).replace('name', 'i.name').replace('code', 'i.code')} = ?
              ORDER BY i.date"""
    with connect() as con:
        return [dict(r) for r in con.execute(sql, (key,))]


def tickets(limit: int = 500) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            """SELECT t.id, t.date, t.store, t.total, COUNT(i.id) AS n_items
               FROM tickets t LEFT JOIN items i ON i.ticket_id = t.id
               GROUP BY t.id ORDER BY t.date DESC LIMIT ?""", (limit,))]


def ticket_detail(ticket_id: str) -> dict | None:
    """Lignes d'un ticket, enrichies du prix le plus ancien connu pour chaque
    référence (toute l'historique confondu), et du total reconstitué si
    chaque article avait été payé à ce prix le plus ancien."""
    with connect() as con:
        ticket = con.execute(
            "SELECT id, date, store, total FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if ticket is None:
            return None
        items = [dict(r) for r in con.execute(
            """SELECT i.code, COALESCE(pl.label, nf.fixed_name, i.name) AS name, i.unit_price,
                      i.quantity, i.is_weight, i.amount, i.discount,
                      COALESCE(NULLIF(i.code, ''), i.name) AS _key
               FROM items i
               LEFT JOIN product_labels pl ON pl.code = i.code AND pl.status = 'matched'
               LEFT JOIN name_fixes nf ON nf.code = i.code
               WHERE i.ticket_id = ? ORDER BY i.id""", (ticket_id,))]

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
                        WHERE COALESCE(NULLIF(code, ''), name) IN ({placeholders})
                    ) WHERE rn = 1""", tuple(keys)).fetchall()
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


def monthly_spending() -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            """SELECT substr(date, 1, 7) AS month, ROUND(SUM(total), 2) AS total,
                      COUNT(*) AS tickets
               FROM tickets GROUP BY month ORDER BY month""")]
