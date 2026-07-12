"""At-rest field encryption for student / registration PII.

Transparent by design: the app encrypts sensitive columns on write and
decrypts them on read, so every page and API keeps working exactly as
before. The protection is against theft of the SQLite file or a backup —
without the key those columns are unreadable ciphertext.

Key: set HIGHERGRADE_ENC_KEY in the environment (on the VM, in
/etc/highergrade.env alongside the other secrets). It can be either a
proper Fernet key (urlsafe-base64, 32 bytes) or any passphrase — a
passphrase is stretched to a key with SHA-256. Generate a strong one with:

    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

⚠️  If the key is lost, the encrypted data cannot be recovered. Back it up.

No key configured  →  enc()/dec() pass values through unchanged, so nothing
breaks; data is simply stored in plaintext (the current behaviour). This is
also what keeps local development working without any setup.
"""

import base64
import hashlib
import hmac
import os

# Version-tagged marker so we can (a) tell encrypted values apart from
# plaintext (mixed states during migration) and (b) evolve the scheme later.
_PREFIX = "enc:v1:"


def _load_fernet():
    raw = (os.environ.get("HIGHERGRADE_ENC_KEY") or "").strip()
    if not raw:
        return None
    try:
        from cryptography.fernet import Fernet
    except Exception:  # noqa: BLE001 — library missing → behave as "no key"
        return None
    key_bytes = raw.encode("utf-8")
    try:
        return Fernet(key_bytes)              # already a valid Fernet key
    except Exception:                          # noqa: BLE001
        # Treat the value as a passphrase: derive a 32-byte key from it.
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


_FERNET = _load_fernet()

# Blind-index HMAC key, derived from the SAME secret so there is only one
# thing to manage. Used for deterministic email lookups without exposing the
# email in the clear. Only meaningful when a key is configured.
_INDEX_KEY = hashlib.sha256(
    ((os.environ.get("HIGHERGRADE_ENC_KEY") or "") + "::blind-index-v1").encode("utf-8")
).digest()


def enabled():
    """True when a usable encryption key is configured."""
    return _FERNET is not None


def enc(value):
    """Encrypt a value for storage. None/blank pass through. Idempotent —
    an already-encrypted value is returned unchanged. When no key is
    configured the value is returned as-is (plaintext)."""
    if value is None or _FERNET is None:
        return value
    if not isinstance(value, str):
        value = str(value)
    if value.startswith(_PREFIX):
        return value
    token = _FERNET.encrypt(value.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def dec(value):
    """Decrypt a stored value. Plaintext / None / non-string pass through
    unchanged, so this is safe on un-migrated rows and mixed data."""
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        return value
    if _FERNET is None:
        return value  # can't decrypt without the key; hand back the token
    try:
        return _FERNET.decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 — corrupt/rotated token: don't crash reads
        return value


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(_PREFIX)


def blind(email):
    """Deterministic lookup index for an email (normalized to lower+trim).
    Returns None for a blank email. Only used when encryption is enabled."""
    if email is None:
        return None
    norm = str(email).strip().lower()
    if not norm:
        return None
    return hmac.new(_INDEX_KEY, norm.encode("utf-8"), hashlib.sha256).hexdigest()
