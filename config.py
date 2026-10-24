"""Configuration de l'application (modifiable via variables d'environnement)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("LIDL_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "lidl.sqlite"
TOKEN_PATH = DATA_DIR / "token.json"   # contient le refresh token : NE PAS partager

COUNTRY = os.getenv("LIDL_COUNTRY", "FR")      # code pays du compte Lidl Plus
LANGUAGE = os.getenv("LIDL_LANGUAGE", "fr")    # langue

# Valider les certificats TLS avec le magasin du système plutôt qu'avec certifi.
# Nécessaire si un antivirus (Avast, Kaspersky…) ou un proxy d'entreprise inspecte le
# HTTPS : son certificat racine n'est que dans le magasin du système (sinon erreur
# « unable to get local issuer certificate »). Inutile sur un serveur.
USE_SYSTEM_CA = os.getenv("LIDL_USE_SYSTEM_CA", "0") == "1"

# Pause entre deux téléchargements de tickets (secondes), pour rester poli avec le serveur
REQUEST_DELAY = float(os.getenv("LIDL_REQUEST_DELAY", "0.4"))

# Pause entre deux requêtes Open Food Facts lors de l'enrichissement des libellés
OFF_REQUEST_DELAY = float(os.getenv("OFF_REQUEST_DELAY", "1.0"))

# --- Portail multi-utilisateur
# Clé Fernet (urlsafe base64, 32 octets) qui chiffre les jetons Lidl en base.
# Si absente, une clé est générée dans data/secret.key (usage local uniquement).
SECRET_KEY = os.getenv("LIDL_SECRET_KEY", "")
SECRET_KEY_PATH = DATA_DIR / "secret.key"

# Code d'invitation exigé à l'inscription (vide = inscription libre)
REGISTRATION_CODE = os.getenv("LIDL_REGISTRATION_CODE", "")

# Cookie de session : mettre LIDL_COOKIE_SECURE=1 derrière HTTPS
COOKIE_SECURE = os.getenv("LIDL_COOKIE_SECURE", "0") == "1"
SESSION_DAYS = int(os.getenv("LIDL_SESSION_DAYS", "30"))
