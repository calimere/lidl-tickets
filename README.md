# Suivi des tickets de caisse Lidl

Outil local (Python + interface web) qui récupère vos tickets Lidl Plus et suit
l'évolution du prix de chaque produit au fil des années.

## Installation

```bash
cd lidl-tickets
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Utilisation

```bash
python app.py
```

Ouvrez http://127.0.0.1:3006 puis :

0. **Créer un compte** du portail (e-mail + mot de passe de 10 caractères minimum).
   Chaque compte a ses propres tickets et son propre lien avec Lidl Plus.
1. **Se connecter à Lidl Plus** : une fenêtre Chromium s'ouvre sur la page de
   connexion Lidl. Saisissez vous-même e-mail/téléphone, mot de passe et code de
   vérification. La fenêtre se ferme toute seule une fois connecté.
2. **Synchroniser** : télécharge tous les tickets (seuls les nouveaux lors des
   synchronisations suivantes).
3. Onglet **Produits & prix** : cliquez sur un produit pour voir la courbe de son
   prix unitaire (prix au kg pour les produits pesés) et chaque achat.
   « par référence » regroupe sur le code article, « par libellé » sur le nom
   (utile si Lidl a changé la référence d'un produit).

Pour tester l'interface sans compte : `python manage.py demo` (données fictives).

## Comptes et migration

- Chaque utilisateur du portail a ses données isolées (`user_id` sur tickets et lignes).
  Le jeton Lidl de chaque compte est chiffré en base (Fernet).
- « Supprimer mon compte » efface le compte, les tickets et le jeton.
- Base d'avant les comptes ? Créez un compte puis rattachez-y l'existant :

```bash
python manage.py adduser moi@example.com
python manage.py adopt moi@example.com    # tickets + data/token.json
```

- Tests : `pip install -r requirements-dev.txt && pytest`.
- Variables : `LIDL_SECRET_KEY` (clé Fernet ; générer avec
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`,
  sinon une clé est créée dans `data/secret.key`), `LIDL_REGISTRATION_CODE`
  (code d'invitation exigé à l'inscription), `LIDL_COOKIE_SECURE=1` (derrière HTTPS),
  `LIDL_SESSION_DAYS`.

## Sécurité et confidentialité

- L'outil ne voit jamais votre mot de passe : il récupère seulement le jeton
  OAuth renvoyé après la connexion.
- Le jeton donne accès à votre compte Lidl Plus : il est stocké chiffré en base
  (clé dans `LIDL_SECRET_KEY` ou `data/secret.key` : ne partagez ni l'un ni l'autre).
  « Déconnecter Lidl » le supprime.
- Tout reste sur votre machine (`data/lidl.sqlite`). Le serveur n'écoute que sur
  127.0.0.1.

## Limites à connaître

L'API Lidl Plus n'est pas officielle (rétro-ingénierie de l'application mobile,
comme les projets open source `lidl-plus`). Lidl peut la modifier sans préavis.
Seuls les tickets présents dans Lidl Plus (achats avec la carte/app) sont récupérables.

Si les lignes produit ne s'affichent pas après une synchronisation, le format
des tickets a probablement changé :

```bash
python manage.py dump <id_ticket>   # voir le JSON brut
# ajuster parser.py
python manage.py reparse            # ré-analyse sans retélécharger
```

Les tickets bruts étant tous conservés, aucune donnée n'est perdue.

## Structure

| Fichier | Rôle |
|---|---|
| `app.py` | serveur FastAPI (API + IHM) |
| `accounts.py` | comptes du portail : mots de passe (argon2), sessions, chiffrement |
| `auth.py` | connexion OAuth/PKCE via Chromium, gestion du jeton Lidl |
| `lidl_client.py` | appels à l'API tickets |
| `parser.py` | extraction des lignes produit d'un ticket |
| `db.py` | base SQLite et requêtes d'analyse |
| `manage.py` | commandes : login, sync, reparse, demo, dump |
| `static/index.html` | interface web (Chart.js) |

Variables d'environnement : `LIDL_COUNTRY` (défaut `FR`), `LIDL_LANGUAGE` (`fr`),
`LIDL_DATA_DIR`, `LIDL_REQUEST_DELAY`.
