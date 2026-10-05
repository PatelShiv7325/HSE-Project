"""Encryption for secrets stored in the database (e.g. government-portal passwords)."""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from sqlalchemy.types import Text, TypeDecorator

PREFIX = "enc::"
log = logging.getLogger(__name__)


def _fernet():
    key = current_app.config.get("FIELD_ENCRYPTION_KEY")
    if not key:   # derive a stable key from SECRET_KEY (so no extra setting is required)
        digest = hashlib.sha256(current_app.config["SECRET_KEY"].encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key if isinstance(key, bytes) else key.encode("utf-8"))


def encrypt_text(value):
    if value is None or value == "" or str(value).startswith(PREFIX):
        return value
    return PREFIX + _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")


def decrypt_text(value):
    if value is None or not str(value).startswith(PREFIX):
        return value          # empty, or an old plain-text value not yet encrypted
    try:
        return _fernet().decrypt(str(value)[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        log.warning("Could not decrypt a stored secret (SECRET_KEY / FIELD_ENCRYPTION_KEY changed?)")
        return ""


class EncryptedString(TypeDecorator):
    """Behaves like a normal string column; stored encrypted, decrypted when read."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_text(value)

    def process_result_value(self, value, dialect):
        return decrypt_text(value)
