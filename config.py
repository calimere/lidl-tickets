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

# Pause entre deux téléchargements de tickets (secondes), pour rester poli avec le serveur
REQUEST_DELAY = float(os.getenv("LIDL_REQUEST_DELAY", "0.4"))

# Pause entre deux requêtes Open Food Facts lors de l'enrichissement des libellés
OFF_REQUEST_DELAY = float(os.getenv("OFF_REQUEST_DELAY", "1.0"))
