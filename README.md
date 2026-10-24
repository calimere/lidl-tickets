# Suivi des tickets de caisse Lidl

Outil local (Python + interface web) qui récupère vos tickets Lidl Plus et suit
l'évolution du prix de chaque produit au fil des années.

## Installation

```bash
cd lidl-tickets
python -m venv .venv
source .venv/bin/activate        # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

## Utilisation

```bash
python app.py
```

Ouvrez http://127.0.0.1:3006 puis :

0. **Créer un compte** du portail (e-mail + mot de passe de 10 caractères minimum).
   Chaque compte a ses propres tickets et son propre lien avec Lidl Plus.
1. **Se connecter à Lidl Plus** : un onglet s'ouvre sur la vraie page de connexion
   Lidl, dans votre navigateur. Saisissez vous-même e-mail/téléphone, mot de passe et
   code de vérification ; l'onglet se ferme tout seul une fois connecté. Cela exige
   l'**extension Chrome** du projet (voir ci-dessous).
2. **Synchroniser** : télécharge tous les tickets (seuls les nouveaux lors des
   synchronisations suivantes).
3. Onglet **Produits & prix** : cliquez sur un produit pour voir la courbe de son
   prix unitaire (prix au kg pour les produits pesés) et chaque achat.
   « par référence » regroupe sur le code article, « par libellé » sur le nom
   (utile si Lidl a changé la référence d'un produit).

Sans compte Lidl : le bouton **« Essayer avec des données fictives »** de l'écran de
connexion ouvre un compte jetable (≈190 tickets fictifs, supprimé après
`LIDL_DEMO_TTL_HOURS` heures, création limitée à 10 par heure et par IP). Il ne peut pas
relier Lidl Plus. En ligne de commande : `python manage.py demo EMAIL` remplit un compte
existant de données fictives.

## Extension Chrome

Le portail ne peut pas récupérer lui-même la redirection finale de Lidl
(`com.lidlplus.app://callback?code=…`, un schéma d'application mobile). L'extension
(`extension/`, Manifest V3) s'en charge :

1. Le portail prépare une demande PKCE avec un `state` à usage unique
   (`POST /api/lidl/start`) et la transmet à l'extension via la page.
2. L'extension ouvre la page de connexion Lidl dans un nouvel onglet ; vous vous
   connectez normalement (mot de passe, SMS, captcha).
3. Elle repère la redirection finale **dans cet onglet uniquement** et envoie
   `{code, state}` à `POST /api/lidl/callback`. Le portail échange le code contre un
   jeton avec le `verifier` gardé côté serveur.

Installation (testeurs) : le portail propose le zip sur `/extension.zip`, à
décompresser puis à charger via `chrome://extensions` (mode développeur →
« Charger l'extension non empaquetée »).

Origines du portail : `extension/manifest.json` (`host_permissions` et
`content_scripts.matches`) liste `lidl-tickets.csk-prod.fr` et `localhost:3006` ;
adaptez-les si le portail change de domaine.

Sécurité : l'extension n'agit que pour une connexion demandée par le portail (message
du content script) et uniquement dans l'onglet qu'elle a ouvert, avec le `state`
annoncé. Sans cela, un site tiers pourrait faire lier le compte Lidl d'une victime au
compte portail de l'attaquant. Tests : `node --test extension/lib.test.js`.

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
  `LIDL_SESSION_DAYS`,
  `LIDL_USE_SYSTEM_CA=1` (si un antivirus type Avast inspecte le HTTPS : erreur
  `unable to get local issuer certificate` lors de la connexion Lidl).

## Données personnelles (RGPD)

- Page publique `/confidentialite` (finalités, durées, droits, hébergement). Elle affiche
  l'éditeur et le contact lus dans `LIDL_PUBLISHER` et `LIDL_CONTACT_EMAIL` : **à
  renseigner avant d'ouvrir le service à d'autres personnes** (sinon « non renseigné »).
- « Exporter mes données » (`GET /api/account/export`, JSON, sans secrets) et
  « Supprimer mon compte » couvrent l'accès, la portabilité et l'effacement.
- Ce texte est un modèle de bonne foi, pas un avis juridique : relisez-le pour votre
  situation.

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
| `demo.py` | tickets fictifs du mode démo |
| `accounts.py` | comptes du portail : mots de passe (argon2), sessions, chiffrement |
| `auth.py` | demande PKCE/échange du code, gestion du jeton Lidl |
| `extension/` | extension Chrome qui capte le code de connexion Lidl |
| `lidl_client.py` | appels à l'API tickets |
| `parser.py` | extraction des lignes produit d'un ticket |
| `db.py` | base SQLite et requêtes d'analyse |
| `manage.py` | commandes : adduser, adopt, sync, reparse, demo, dump |
| `static/index.html` | interface web (Chart.js) |

Variables d'environnement : `LIDL_COUNTRY` (défaut `FR`), `LIDL_LANGUAGE` (`fr`),
`LIDL_DATA_DIR`, `LIDL_REQUEST_DELAY`.
