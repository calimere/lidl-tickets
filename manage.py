"""Commandes utilitaires en ligne de commande.

  python manage.py adduser EMAIL      # crée un compte du portail (mot de passe demandé)
  python manage.py adopt EMAIL        # rattache à ce compte la base mono-utilisateur
                                      # d'avant les comptes (tickets + data/token.json)
  python manage.py sync EMAIL         # télécharge les nouveaux tickets du compte
  python manage.py reparse            # ré-analyse les tickets bruts déjà stockés
  python manage.py demo EMAIL         # remplit le compte avec des données fictives
  python manage.py dump ID            # affiche le JSON brut d'un ticket (pour ajuster parser.py)
  python manage.py enrich [--force] [--limit N]
                             # cherche un libellé complet sur Open Food Facts
                             # pour chaque référence produit (--force : relance
                             # aussi les références déjà traitées)
  python manage.py fixnames # tente de deviner les lettres accentuées perdues
                             # (caractère �) par motif contre notre propre
                             # vocabulaire ; sinon retire juste le caractère
"""
import getpass
import json
import random
import sys
from datetime import datetime, timedelta

import accounts
import config
import db
from auth import TokenStore


def _user_id(email: str) -> int:
    user = db.user_by_email(email.strip().lower())
    if user is None:
        sys.exit(f"Compte inconnu : {email} (python manage.py adduser {email})")
    return user["id"]


def cmd_adduser(email):
    password = getpass.getpass("Mot de passe : ")
    accounts.register(email, password)
    print(f"Compte créé : {email}")


def cmd_adopt(email):
    user_id = _user_id(email)
    tickets, items = db.adopt_legacy(user_id)
    print(f"{tickets} ticket(s), {items} ligne(s) rattaché(s) à {email}.")
    legacy_token = config.DATA_DIR / "token.json"
    if legacy_token.exists():
        TokenStore(user_id).save(json.loads(legacy_token.read_text()))
        legacy_token.unlink()
        print("Jeton Lidl importé (chiffré) et data/token.json supprimé.")


def cmd_sync(email):
    import app
    user_id = _user_id(email)
    app._sync_job(user_id)
    print(app.state_of(user_id)["sync"])


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


def cmd_demo(email):
    import demo
    print(f"{demo.populate(_user_id(email))} tickets fictifs créés.")


if __name__ == "__main__":
    db.init()
    cmds = {"adduser": cmd_adduser, "adopt": cmd_adopt,
            "sync": cmd_sync, "reparse": cmd_reparse, "demo": cmd_demo,
            "dump": cmd_dump, "enrich": cmd_enrich, "fixnames": cmd_fixnames}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]](*sys.argv[2:])
