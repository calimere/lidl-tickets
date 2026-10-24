"""Données fictives pour essayer le portail sans compte Lidl Plus."""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import db

DEMO_DOMAIN = "demo.invalid"   # domaine réservé (RFC 2606) : jamais un vrai e-mail

# (référence, libellé, prix de départ, vendu au poids)
CATALOG = [
    ("0012345", "Lait demi-écrémé 1L", 0.89, False),
    ("0023456", "Beurre doux 250g", 1.79, False),
    ("0034567", "Café moulu 250g", 2.49, False),
    ("0045678", "Bananes", 1.69, True),
    ("0056789", "Pâtes penne 500g", 0.75, False),
    ("0067890", "Huile d'olive 1L", 5.49, False),
    ("0078901", "Pain de mie", 1.15, False),
    ("0089012", "Oeufs x12", 2.35, False),
    ("0090123", "Poulet fermier", 7.90, True),
    ("0101234", "Yaourt nature x8", 1.45, False),
]


def is_demo_email(email: str) -> bool:
    return email.endswith("@" + DEMO_DOMAIN)


def populate(user_id: int, start: datetime = datetime(2022, 1, 8),
             end: datetime | None = None, seed: int = 1) -> int:
    """Crée des tickets fictifs (hausse de prix d'environ 6 % par an). Retourne leur nombre."""
    rng = random.Random(seed)
    end = end or datetime.now()
    tickets = []
    day, n = start, 0
    while day < end:
        years = (day - start).days / 365
        lines = []
        for code, name, base, weight in rng.sample(CATALOG, k=rng.randint(3, 6)):
            price = round(base * (1 + 0.06 * years + rng.uniform(-0.03, 0.03)), 2)
            qty = round(rng.uniform(0.6, 1.8), 3) if weight else rng.randint(1, 3)
            lines.append({"codeInput": code, "description": name,
                          "currentUnitPrice": f"{price:.2f}".replace(".", ","),
                          "quantity": str(qty).replace(".", ","), "isWeight": weight,
                          "originalAmount": f"{price * qty:.2f}".replace(".", ","),
                          "discounts": []})
        total = sum(float(line["originalAmount"].replace(",", ".")) for line in lines)
        tid = f"DEMO{n:05d}"
        detail = {"id": tid, "date": day.isoformat(timespec="seconds"),
                  "totalAmount": round(total, 2),
                  "store": {"name": "Lidl Démo", "locality": "Marseille"}, "itemsLine": lines}
        tickets.append(({"id": tid}, detail))
        day += timedelta(days=rng.randint(5, 12))
        n += 1
    db.save_tickets(user_id, tickets)
    return n
