"""Client minimal de l'API tickets de Lidl Plus (non officielle, rétro-ingénierie).

Lidl peut modifier cette API à tout moment ; les tickets bruts sont donc
toujours conservés en base pour pouvoir être ré-analysés sans re-téléchargement.
"""
from __future__ import annotations

import math
from typing import Iterator

import requests

import config
from auth import TokenStore

TICKETS_API = "https://tickets.lidlplus.com/api/v2"
TICKETS_API_V3 = "https://tickets.lidlplus.com/api/v3"


class LidlClient:
    def __init__(self, store: TokenStore, country: str = config.COUNTRY,
                 language: str = config.LANGUAGE):
        self.store = store
        self.country = country.upper()
        self.language = language.lower()
        self.session = requests.Session()

    def _headers(self, force_refresh=False) -> dict:
        # Les en-têtes "App-Version"/"Operating-System"/"App" imitant l'app
        # mobile (valeur placeholder "999.99.9") font tarpitter la requête
        # par la protection anti-bot de Lidl, jeton valide ou non. Le
        # Bearer seul suffit et répond normalement.
        return {
            "Authorization": f"Bearer {self.store.access_token(force_refresh)}",
            "Accept-Language": self.language,
        }

    def _get(self, url: str) -> dict:
        r = self.session.get(url, headers=self._headers(), timeout=30)
        if r.status_code == 401:  # jeton expiré côté serveur : on rafraîchit une fois
            r = self.session.get(url, headers=self._headers(force_refresh=True), timeout=30)
        r.raise_for_status()
        return r.json()

    def ticket_page(self, page: int = 1) -> dict:
        return self._get(f"{TICKETS_API}/{self.country}/tickets"
                         f"?pageNumber={page}&onlyFavorite=false")

    def iter_ticket_summaries(self) -> Iterator[dict]:
        first = self.ticket_page(1)
        yield from first.get("tickets", [])
        size = first.get("size") or len(first.get("tickets", [])) or 25
        pages = math.ceil((first.get("totalCount") or 0) / size)
        for page in range(2, pages + 1):
            yield from self.ticket_page(page).get("tickets", [])

    def ticket(self, ticket_id: str) -> dict:
        try:
            return self._get(f"{TICKETS_API}/{self.country}/tickets/{ticket_id}")
        except requests.HTTPError as e:
            # Les tickets "HTML" (reçu imprimé, pas de lignes JSON structurées)
            # sont refusés en 400 par l'endpoint v2 ; seul le v3 les sert.
            if e.response is not None and e.response.status_code == 400:
                return self._get(f"{TICKETS_API_V3}/{self.country}/tickets/{ticket_id}")
            raise
