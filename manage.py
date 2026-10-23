"""Commandes utilitaires en ligne de commande.

  python manage.py login     # connexion (ouvre Chromium)
  python manage.py sync      # télécharge les nouveaux tickets
  python manage.py reparse   # ré-analyse les tickets bruts déjà stockés
  python manage.py demo      # remplit la base avec des données fictives (test de l'IHM)
  python manage.py dump ID   # affiche le JSON brut d'un ticket (pour ajuster parser.py)
  python manage.py enrich [--force] [--limit N]
                             # cherche un libellé complet sur Open Food Facts
                             # pour chaque référence produit (--force : relance
                             # aussi les références déjà traitées)
  python manage.py fixnames # tente de deviner les lettres accentuées perdues
                             # (caractère �) par motif contre notre propre
                             # vocabulaire ; sinon retire juste le caractère
"""
import json
import random
import sys
from datetime import datetime, timedelta

import db
from auth import TokenStore, interactive_login


def cmd_login():
    interactive_login(TokenStore())
    print("Connecté, jeton enregistré.")


def cmd_sync():
    import app
    app._sync_job()
    print(app.state["sync"])


def cmd_reparse():
    print("Tickets / lignes : %s / %s" % db.reparse_all())


def cmd_dump(ticket_id):
    with db.connect() as con:
        r = con.execute("SELECT raw_detail FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    print(json.dumps(json.loads(r[0]), indent=2, ensure_ascii=False) if r else "Introuvable")


def cmd_enrich(*args):
    import off_enrich
    force = "--force" in args
    limit = None
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    stats = off_enrich.enrich_all(limit=limit, force=force)
    print(f"{stats['total']} référence(s) traitée(s) : "
          f"{stats['matched']} libellé(s) trouvé(s), {stats['unmatched']} sans correspondance fiable.")


def cmd_fixnames():
    import fix_mojibake
    stats = fix_mojibake.fix_all()
    print(f"{stats['total']} nom(s) corrompu(s) traité(s) : "
          f"{stats['guessed']} deviné(s) par vocabulaire, {stats['stripped']} juste nettoyé(s).")


def cmd_demo():
    random.seed(1)
    catalog = [("0012345", "Lait demi-écrémé 1L", 0.89, False),
               ("0023456", "Beurre doux 250g", 1.79, False),
               ("0034567", "Café moulu 250g", 2.49, False),
               ("0045678", "Bananes", 1.69, True),
               ("0056789", "Pâtes penne 500g", 0.75, False),
               ("0067890", "Huile d'olive 1L", 5.49, False),
               ("0078901", "Pain de mie", 1.15, False)]
    day = datetime(2022, 1, 8)
    n = 0
    while day < datetime(2026, 10, 1):
        years = (day - datetime(2022, 1, 1)).days / 365
        lines = []
        for code, name, base, weight in random.sample(catalog, k=random.randint(3, 6)):
            price = round(base * (1 + 0.06 * years + random.uniform(-0.03, 0.03)), 2)
            qty = round(random.uniform(0.6, 1.8), 3) if weight else random.randint(1, 3)
            lines.append({"codeInput": code, "description": name,
                          "currentUnitPrice": f"{price:.2f}".replace(".", ","),
                          "quantity": str(qty).replace(".", ","), "isWeight": weight,
                          "originalAmount": f"{price*qty:.2f}".replace(".", ","),
                          "discounts": []})
        total = sum(float(l["originalAmount"].replace(",", ".")) for l in lines)
        tid = f"DEMO{n:05d}"
        detail = {"id": tid, "date": day.isoformat(), "totalAmount": round(total, 2),
                  "store": {"name": "Lidl Démo", "locality": "Marseille"}, "itemsLine": lines}
        db.save_ticket({"id": tid}, detail)
        day += timedelta(days=random.randint(5, 12))
        n += 1
    print(f"{n} tickets fictifs créés.")


if __name__ == "__main__":
    db.init()
    cmds = {"login": cmd_login, "sync": cmd_sync, "reparse": cmd_reparse,
            "demo": cmd_demo, "dump": cmd_dump, "enrich": cmd_enrich,
            "fixnames": cmd_fixnames}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]](*sys.argv[2:])
