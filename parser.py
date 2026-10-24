"""Transforme un ticket brut Lidl Plus en lignes produit normalisées.

Deux formats ont été observés :
  1. JSON : liste `itemsLine` (description/name, currentUnitPrice, quantity,
     isWeight, originalAmount, codeInput, discounts[]).
  2. HTML : champ `htmlPrintedReceipt` (ticket imprimé en HTML). Analyse
     heuristique via les attributs `data-art-*` ; à ajuster si Lidl change le format,
     puis relancer `python manage.py reparse`.
"""
from __future__ import annotations

import re
from typing import Any


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("\u00a0", "").replace(" ", "").replace("€", "")
    s = s.replace(",", ".")
    m = re.search(r"-?\d+(\.\d+)?", s)
    return float(m.group()) if m else None


def ticket_meta(summary: dict, detail: dict) -> dict:
    store = detail.get("store") or summary.get("store") or {}
    if isinstance(store, dict):
        store_name = " ".join(x for x in [store.get("name"), store.get("locality")] if x)
    else:
        store_name = str(store)
    return {
        "id": detail.get("id") or summary.get("id"),
        "date": (detail.get("date") or summary.get("date") or "")[:19],
        "store": store_name,
        "total": to_float(detail.get("totalAmount") or summary.get("totalAmount")),
    }


def _json_items(lines: list[dict]) -> list[dict]:
    items = []
    for line in lines:
        qty = to_float(line.get("quantity")) or 1.0
        amount = to_float(line.get("originalAmount"))
        unit = to_float(line.get("currentUnitPrice"))
        if unit is None and amount is not None:
            unit = amount / qty if qty else amount
        discount = sum(to_float(d.get("amount")) or 0 for d in (line.get("discounts") or []))
        items.append({
            "code": str(line.get("codeInput") or "").strip(),
            "name": (line.get("description") or line.get("name") or "").strip(),
            "unit_price": unit,
            "quantity": qty,
            "is_weight": 1 if line.get("isWeight") else 0,
            "amount": amount,
            "discount": round(discount, 2),
        })
    return items


_SYMBOLS = "®°™©²³½ºª"   # symboles qui peuvent aussi être perdus dans les libellés


def recover_accents(description: str, line_text: str) -> str:
    """Répare les « � » de `data-art-description` grâce au texte visible de la ligne.

    Lidl envoie l'attribut avec les lettres accentuées déjà perdues
    (`Brioche tranch�e`), alors que le texte imprimé juste à côté est correct
    (`Brioche tranch&eacute;e   2,09  1  2,09 A T`). On aligne la description sur le
    début de la ligne : chaque « � » correspond au caractère qui est à la même place.
    Si l'alignement n'est pas sûr (ligne différente, caractère qui n'est pas une
    lettre), la description est rendue telle quelle."""
    if "\ufffd" not in description:
        return description
    text = line_text.lstrip()
    # Le texte imprim\u00e9 tronque les noms longs : on s'aligne alors sur la partie visible.
    visible = re.split(r"\s{2,}", text, maxsplit=1)[0]
    head = description[:len(visible)] if len(visible) < len(description) else description
    pattern = "".join("(.)" if ch == "\ufffd" else re.escape(ch) for ch in head)
    m = re.match(pattern, text)
    if not m or not all(c.isalpha() or c in _SYMBOLS for c in m.groups()):
        return description
    letters = iter(m.groups())
    return ("".join(next(letters) if ch == "\ufffd" else ch for ch in head)
            + description[len(head):])


def _html_items(html: str) -> list[dict]:
    """Analyse le reçu imprimé (format `<span class="article" data-art-id=...
    data-art-description=... data-art-quantity=... data-unit-price=...>`).
    Les remises suivent dans des `<span class="discount" data-promotion-id=...>`
    séparés, rattachés ici au dernier article rencontré (ordre du document)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    current: dict | None = None

    for tag in soup.find_all("span"):
        classes = tag.attrs.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        if "article" in classes:
            art_id = tag.attrs.get("data-art-id") or ""
            description = (tag.attrs.get("data-art-description") or "").strip()
            if current is not None and current["code"] == art_id and current["_raw_name"] == description:
                # Produits au poids : ligne "montant" puis ligne "qté kg x prix/kg"
                # répétant les mêmes attributs — c'est la suite du même article,
                # pas un second achat. Pour ces produits la ligne du nom peut venir
                # après celle des kilos : on en profite pour réparer les accents.
                if "�" in current["name"]:
                    current["name"] = recover_accents(description, tag.get_text())
                continue
            qty_raw = tag.attrs.get("data-art-quantity")
            qty = to_float(qty_raw) or 1.0
            unit = to_float(tag.attrs.get("data-unit-price") or tag.attrs.get("data-art-unit-price"))
            current = {
                "code": str(art_id).strip(),
                "name": recover_accents(description, tag.get_text()),
                "_raw_name": description,   # pour reconnaître la 2e ligne d'un produit au poids
                "unit_price": unit,
                "quantity": qty,
                "is_weight": 1 if any(c in (qty_raw or "") for c in ",.") else 0,
                "amount": unit * qty if unit is not None else None,
                "discount": 0.0,
            }
            items.append(current)
        elif "discount" in classes and current is not None:
            # Selon les reçus, le montant est soit seul dans sa balise, soit
            # en fin de ligne ("     Réduction Lidl Plus           -4,75").
            text = tag.get_text(strip=True)
            m = re.search(r"-?\d+[,.]\d{2}$", text)
            if m:
                current["discount"] = round(current["discount"] + abs(to_float(m.group()) or 0), 2)

    return items


def parse_items(detail: dict) -> list[dict]:
    if detail.get("itemsLine"):
        items = _json_items(detail["itemsLine"])
    elif detail.get("htmlPrintedReceipt"):
        items = _html_items(detail["htmlPrintedReceipt"])
    else:
        items = []
    return [{k: v for k, v in i.items() if k != "_raw_name"}
            for i in items if i["name"] and i["unit_price"] is not None]
