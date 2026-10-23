"""Enrichit les libellés produit (tronqués sur les tickets Lidl) via Open Food
Facts. Les codes articles Lidl ne sont pas des codes-barres EAN : on cherche
donc par nom (recherche texte plein-texte) et on ne garde le résultat que si
son nom recoupe suffisamment le nôtre.

Note : l'ancien endpoint `cgi/search.pl` renvoie des 503 (API historique en
fin de vie côté Open Food Facts) ; on utilise la nouvelle recherche
`search.openfoodfacts.org`.

Usage : python manage.py enrich
"""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Optional

import requests

import config
import db

SEARCH_URL = "https://search.openfoodfacts.org/search"
USER_AGENT = "lidl-tickets-local/1.0 (outil personnel, usage non commercial)"

STOPWORDS = {
    "de", "du", "des", "le", "la", "les", "un", "une", "au", "aux", "en",
    "et", "a", "pour", "sans", "avec", "x", "gr", "g", "kg", "l", "ml", "cl",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _tokens(name: str) -> list[str]:
    name = (name or "").replace("�", " ")
    name = _strip_accents(name.lower())
    words = re.findall(r"[a-z0-9]+", name)
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def _score(query_tokens: list[str], candidate: str) -> tuple[float, int, float]:
    cand_tokens = _tokens(candidate)
    cand = set(cand_tokens)
    q = set(query_tokens)
    matched = len(q & cand)
    coverage = matched / len(q) if q else 0.0
    # précision : à couverture égale, préfère "Beurre doux" (générique, précis)
    # à "Le Beurre De Condé-Sur-Vire Doux" (marque précise mais qui contient
    # aussi nos mots par coïncidence).
    precision = matched / len(cand_tokens) if cand_tokens else 0.0
    return coverage, matched, coverage * precision


def _is_good_match(coverage: float, matched: int, n_query_tokens: int) -> bool:
    if n_query_tokens <= 1:
        return matched == 1 and coverage == 1.0
    return coverage >= 0.6 and matched >= 2


def _search(session: requests.Session, name: str) -> list[dict]:
    params = {
        "q": name,
        "page_size": 10,
        "fields": "code,product_name,product_name_fr,brands",
    }
    r = session.get(SEARCH_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    return r.json().get("hits", [])


def _brands_str(brands) -> str:
    if isinstance(brands, list):
        return ", ".join(str(b) for b in brands)
    return str(brands or "")


def best_match(session: requests.Session, name: str) -> Optional[dict]:
    q = _tokens(name)
    if not q:
        return None
    try:
        candidates = _search(session, name)
    except requests.RequestException:
        return None
    best = None
    for c in candidates:
        label = (c.get("product_name_fr") or c.get("product_name") or "").strip()
        if not label:
            continue
        coverage, matched, combined = _score(q, label)
        if _is_good_match(coverage, matched, len(q)) and (best is None or combined > best[0]):
            best = (combined, coverage, c, label)
    if not best:
        return None
    combined, coverage, c, label = best
    return {"label": label, "brands": _brands_str(c.get("brands")), "off_code": c.get("code") or "",
            "score": round(coverage, 2)}


def enrich_all(limit: int | None = None, force: bool = False) -> dict:
    codes = db.codes_needing_enrichment(force=force)
    if limit:
        codes = codes[:limit]
    session = requests.Session()
    matched = unmatched = 0
    for i, (code, name) in enumerate(codes, 1):
        result = best_match(session, name)
        if result:
            db.save_label(code, status="matched", **result)
            matched += 1
        else:
            db.save_label(code, label=None, brands=None, off_code=None, score=None, status="unmatched")
            unmatched += 1
        if i < len(codes):
            time.sleep(config.OFF_REQUEST_DELAY)
    return {"total": len(codes), "matched": matched, "unmatched": unmatched}
