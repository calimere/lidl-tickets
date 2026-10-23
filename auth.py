"""Authentification Lidl Plus (OAuth2 + PKCE, comme l'application mobile).

Principe : on ouvre une vraie fenêtre Chromium sur la page de connexion Lidl.
C'est VOUS qui saisissez identifiant, mot de passe et code de vérification
(SMS/e-mail, captcha éventuel). L'outil ne voit jamais votre mot de passe :
il intercepte seulement la redirection finale contenant le code d'autorisation,
puis l'échange contre un jeton (refresh token) stocké localement dans data/token.json.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests

import config

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
    def __init__(self, path=config.TOKEN_PATH):
        self.path = path
        self.data: dict = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
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
        self.path.write_text(json.dumps(self.data, indent=2))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def clear(self) -> None:
        self.data = {}
        if self.path.exists():
            self.path.unlink()

    def access_token(self, force_refresh: bool = False) -> str:
        if not self.logged_in:
            raise AuthError("Non connecté : lancez la connexion Lidl d'abord.")
        if force_refresh or time.time() >= self.data.get("expires_at", 0):
            self.refresh()
        return self.data["access_token"]

    def refresh(self) -> None:
        r = requests.post(f"{ACCOUNTS_URL}/connect/token", headers=_basic_auth_header(),
                          data={"grant_type": "refresh_token",
                                "refresh_token": self.data["refresh_token"]},
                          timeout=30)
        if r.status_code != 200:
            raise AuthError(f"Rafraîchissement du jeton refusé ({r.status_code}). "
                            "Reconnectez-vous.")
        self.save(r.json())


# --------------------------------------------------------------------------- connexion
def interactive_login(store: TokenStore, country: str = config.COUNTRY,
                      language: str = config.LANGUAGE, timeout: int = 600) -> None:
    """Ouvre Chromium, attend que l'utilisateur se connecte, récupère le jeton."""
    from playwright.sync_api import sync_playwright

    verifier, challenge = _pkce_pair()
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "Country": country.upper(),
        "language": f"{language.lower()}-{country.upper()}",
    }
    authorize_url = f"{ACCOUNTS_URL}/connect/authorize?{urlencode(params)}"
    found: dict = {}

    def inspect(url: str | None) -> None:
        if url and url.startswith(REDIRECT_URI) and "code" not in found:
            code = parse_qs(urlparse(url).query).get("code")
            if code:
                found["code"] = code[0]

    with sync_playwright() as p:
        # channel="chrome" : utilise votre vrai Chrome installé plutôt que le
        # Chromium embarqué de Playwright, et on retire les marqueurs
        # d'automatisation les plus visibles (navigator.webdriver, bannière
        # "contrôlé par un logiciel de test"), car la page de connexion Lidl
        # les détecte et affiche une fausse page de "surcapacité" à la place
        # du vrai formulaire.
        try:
            browser = p.chromium.launch(
                headless=False,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
        except Exception:
            # Chrome non installé : repli sur le Chromium embarqué
            browser = p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
        page = browser.new_page()
        # La redirection vers com.lidlplus.app:// arrive dans l'en-tête Location
        page.on("response", lambda r: inspect(r.headers.get("location")))
        page.on("request", lambda r: inspect(r.url))
        page.on("framenavigated", lambda f: inspect(f.url))
        try:
            page.goto(authorize_url)
        except Exception:
            pass
        deadline = time.time() + timeout
        while "code" not in found and time.time() < deadline:
            try:
                if page.is_closed():
                    break
                page.wait_for_timeout(500)
            except Exception:
                break
        try:
            browser.close()
        except Exception:
            pass

    if "code" not in found:
        raise AuthError("Connexion annulée ou expirée (fenêtre fermée avant la fin).")

    r = requests.post(f"{ACCOUNTS_URL}/connect/token", headers=_basic_auth_header(),
                      data={"grant_type": "authorization_code",
                            "code": found["code"],
                            "redirect_uri": REDIRECT_URI,
                            "code_verifier": verifier},
                      timeout=30)
    if r.status_code != 200:
        raise AuthError(f"Échange du code refusé ({r.status_code}) : {r.text[:200]}")
    store.save(r.json())
