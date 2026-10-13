"""Encryption for secrets stored in the database (e.g. government-portal passwords)."""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from flask import current_app
from sqlalchemy.types import Text, TypeDecorator

PREFIX = "enc::"
log = logging.getLogger(__name__)


def _fernet():
    """Encrypts with FIELD_ENCRYPTION_KEY when set, and can ALWAYS still read values that were
    encrypted with the old SECRET_KEY-derived key. So you can add FIELD_ENCRYPTION_KEY later
    (or rotate SECRET_KEY) without losing the stored portal passwords."""
    digest = hashlib.sha256(current_app.config["SECRET_KEY"].encode("utf-8")).digest()
    keys = [base64.urlsafe_b64encode(digest)]            # old / fallback key
    explicit = current_app.config.get("FIELD_ENCRYPTION_KEY")
    if explicit:
        explicit = explicit if isinstance(explicit, bytes) else explicit.encode("utf-8")
        keys.insert(0, explicit)                          # first key = used for new encryption
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "FIELD_ENCRYPTION_KEY is not a valid key. Create one with:  python -c "
            "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from exc


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
