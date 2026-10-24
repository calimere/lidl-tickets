"""Authentification Lidl Plus (OAuth2 + PKCE, comme l'application mobile).

Principe : le portail prépare une demande d'autorisation (PKCE + `state`) et
l'utilisateur se connecte sur la vraie page Lidl, dans son propre navigateur
(mot de passe, SMS, captcha : le portail ne les voit jamais). L'extension Chrome
du projet repère la redirection finale `com.lidlplus.app://callback?code=...`
et renvoie le code au portail, qui l'échange contre un jeton (refresh token)
stocké chiffré en base pour l'utilisateur.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from cryptography.fernet import InvalidToken

import accounts
import config
import db

if config.USE_SYSTEM_CA:
    import truststore
    truststore.inject_into_ssl()

ACCOUNTS_URL = "https://accounts.lidl.com"
CLIENT_ID = "LidlPlusNativeClient"
REDIRECT_URI = "com.lidlplus.app://callback"
SCOPE = "openid profile offline_access lpprofile lpapis"


class AuthError(Exception):
    pass


# --------------------------------------------------------------------------- PKCE
def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _basic_auth_header() -> dict:
    secret = base64.b64encode(f"{CLIENT_ID}:secret".encode()).decode()
    return {"Authorization": f"Basic {secret}",
            "Content-Type": "application/x-www-form-urlencoded"}


# --------------------------------------------------------------------------- stockage
class TokenStore:
    """Jeton Lidl d'un utilisateur du portail, chiffré en base (Fernet)."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.data: dict = {}
        payload = db.load_lidl_token(user_id)
        if payload:
            try:
                self.data = json.loads(accounts.decrypt(payload))
            except (InvalidToken, json.JSONDecodeError):
                self.data = {}

    @property
    def logged_in(self) -> bool:
        return bool(self.data.get("refresh_token"))

    def save(self, token_response: dict) -> None:
        self.data = {
            "access_token": token_response["access_token"],
            "refresh_token": token_response.get("refresh_token", self.data.get("refresh_token")),
            "expires_at": time.time() + int(token_response.get("expires_in", 3600)) - 60,
        }
        db.save_lidl_token(self.user_id, accounts.encrypt(json.dumps(self.data)))

    def clear(self) -> None:
        self.data = {}
        db.delete_lidl_token(self.user_id)

    def access_token(self, force_refresh: bool = False) -> str:
        if not self.logged_in:
            raise AuthError("Non connecté : lancez la connexion Lidl d'abord.")
        if force_refresh or time.time() >= self.data.get("expires_at", 0):
            self.refresh()
        return self.data["access_token"]

    def refresh(self) -> None:
        try:
            r = requests.post(f"{ACCOUNTS_URL}/connect/token", headers=_basic_auth_header(),
                              data={"grant_type": "refresh_token",
                                    "refresh_token": self.data["refresh_token"]},
                              timeout=30)
        except requests.RequestException as e:
            raise AuthError(f"Lidl injoignable ({type(e).__name__}).") from e
        if r.status_code != 200:
            raise AuthError(f"Rafraîchissement du jeton refusé ({r.status_code}). "
                            "Reconnectez-vous.")
        self.save(r.json())


# --------------------------------------------------------------------------- connexion
AUTH_REQUEST_TTL = 600   # secondes : durée de validité d'une demande de connexion


def _hash_state(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def start_authorization(user_id: int, country: str = config.COUNTRY,
                        language: str = config.LANGUAGE) -> tuple[str, str]:
    """Prépare une connexion Lidl pour cet utilisateur. Retourne (url_autorisation, state).

    Le `state` est un secret à usage unique : il est la seule preuve, côté
    callback, que le code reçu appartient bien à cet utilisateur."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "Country": country.upper(),
        "language": f"{language.lower()}-{country.upper()}",
    }
    db.save_auth_request(_hash_state(state), user_id, accounts.encrypt(verifier),
                         time.time() + AUTH_REQUEST_TTL)
    return f"{ACCOUNTS_URL}/connect/authorize?{urlencode(params)}", state


def complete_authorization(state: str, code: str) -> int:
    """Échange le code reçu par l'extension contre un jeton. Retourne l'user_id."""
    req = db.pop_auth_request(_hash_state(state), time.time())
    if req is None:
        raise AuthError("Demande de connexion inconnue ou expirée : recommencez.")
    try:
        r = requests.post(f"{ACCOUNTS_URL}/connect/token", headers=_basic_auth_header(),
                          data={"grant_type": "authorization_code",
                                "code": code,
                                "redirect_uri": REDIRECT_URI,
                                "code_verifier": accounts.decrypt(req["verifier"])},
                          timeout=30)
    except requests.RequestException as e:
        # Le code Lidl est à usage unique et le state est consommé : il faut recommencer.
        raise AuthError(f"Lidl injoignable depuis le serveur ({type(e).__name__}) : "
                        "relancez la connexion.") from e
    if r.status_code != 200:
        raise AuthError(f"Échange du code refusé par Lidl ({r.status_code}).")
    TokenStore(req["user_id"]).save(r.json())
    return req["user_id"]
