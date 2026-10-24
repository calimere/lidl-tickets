"""Comptes du portail : mots de passe, sessions par cookie, chiffrement des jetons Lidl.

Distinct de auth.py, qui gère la connexion au compte *Lidl* de chaque utilisateur.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet

import config
import db

COOKIE_NAME = "session"
MIN_PASSWORD_LEN = 10
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_hasher = PasswordHasher()
# Hash factice pour que la durée de réponse ne révèle pas si l'e-mail existe
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


class AccountError(Exception):
    """Erreur affichable à l'utilisateur (message en français)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- chiffrement
_fernet: Fernet | None = None


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = config.SECRET_KEY
        if not key:
            if config.SECRET_KEY_PATH.exists():
                key = config.SECRET_KEY_PATH.read_text().strip()
            else:
                key = Fernet.generate_key().decode()
                config.SECRET_KEY_PATH.write_text(key)
                try:
                    config.SECRET_KEY_PATH.chmod(0o600)
                except OSError:
                    pass
        _fernet = Fernet(key.encode())
    return _fernet


def encrypt(text: str) -> str:
    return fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return fernet().decrypt(token.encode()).decode()


# --------------------------------------------------------------------------- anti brute-force
class Throttle:
    """Limite en mémoire : `limit` échecs par fenêtre de `window` secondes et par clé."""

    def __init__(self, limit: int = 8, window: int = 300):
        self.limit, self.window = limit, window
        self._fails: dict[str, deque] = defaultdict(deque)

    def _prune(self, key: str) -> deque:
        q = self._fails[key]
        while q and q[0] < time.time() - self.window:
            q.popleft()
        return q

    def check(self, key: str) -> None:
        if len(self._prune(key)) >= self.limit:
            raise AccountError("Trop de tentatives, réessayez dans quelques minutes.", 429)

    def fail(self, key: str) -> None:
        self._prune(key).append(time.time())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


login_throttle = Throttle()


# --------------------------------------------------------------------------- comptes
def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise AccountError("Adresse e-mail invalide.")
    return email


def register(email: str, password: str, invite_code: str = "") -> int:
    if config.REGISTRATION_CODE and not secrets.compare_digest(
            invite_code.encode(), config.REGISTRATION_CODE.encode()):
        raise AccountError("Code d'invitation invalide.", 403)
    email = normalize_email(email)
    if len(password) < MIN_PASSWORD_LEN:
        raise AccountError(f"Mot de passe trop court ({MIN_PASSWORD_LEN} caractères minimum).")
    if len(password) > 256:
        raise AccountError("Mot de passe trop long.")
    try:
        return db.create_user(email, _hasher.hash(password))
    except sqlite3.IntegrityError:
        raise AccountError("Un compte existe déjà avec cet e-mail.", 409) from None


def authenticate(email: str, password: str, throttle_key: str) -> int:
    login_throttle.check(throttle_key)
    user = db.user_by_email((email or "").strip().lower())
    try:
        _hasher.verify(user["password_hash"] if user else _DUMMY_HASH, password)
        ok = user is not None
    except (VerifyMismatchError, InvalidHashError):
        ok = False
    if not ok:
        login_throttle.fail(throttle_key)
        raise AccountError("E-mail ou mot de passe incorrect.", 401)
    login_throttle.reset(throttle_key)
    if _hasher.check_needs_rehash(user["password_hash"]):
        db.set_password_hash(user["id"], _hasher.hash(password))
    return user["id"]


# --------------------------------------------------------------------------- sessions
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.save_session(_hash_token(token), user_id, time.time() + config.SESSION_DAYS * 86400)
    return token


def user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    return db.user_for_session(_hash_token(token), time.time())


def end_session(token: str | None) -> None:
    if token:
        db.delete_session(_hash_token(token))
