"""Web push notifications — VAPID key management and send helpers.

Keys are generated once on first use and stored in data/ (private PEM +
public key). Requires: pip install pywebpush
"""

import json
import logging
from pathlib import Path

import database

DATA_DIR = Path(__file__).parent / "data"
PRIVATE_PEM_PATH = DATA_DIR / "vapid_private.pem"
PUBLIC_KEY_PATH = DATA_DIR / "vapid_public.txt"
VAPID_CLAIMS = {"sub": "mailto:abwalker417@gmail.com"}


def _ensure_keys():
    """Generate VAPID keys on first use. Returns (private_pem_path, public_key_b64)."""
    if PRIVATE_PEM_PATH.exists() and PUBLIC_KEY_PATH.exists():
        return str(PRIVATE_PEM_PATH), PUBLIC_KEY_PATH.read_text().strip()

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from py_vapid import b64urlencode

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    raw_pub = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = b64urlencode(raw_pub)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_PEM_PATH.write_bytes(private_pem)
    PUBLIC_KEY_PATH.write_text(public_b64)
    try:
        PRIVATE_PEM_PATH.chmod(0o600)
    except OSError:
        pass
    return str(PRIVATE_PEM_PATH), public_b64


def get_public_key():
    return _ensure_keys()[1]


def is_available():
    try:
        import pywebpush  # noqa: F401
        return True
    except ImportError:
        return False


def send_to_user(user_id, title, body, url="/"):
    """Send a push to every subscribed browser for a user. Prunes dead endpoints.

    Returns the number of successful sends."""
    from pywebpush import webpush, WebPushException
    pem_path, _ = _ensure_keys()
    sent = 0
    for sub in database.get_push_subscriptions(user_id):
        try:
            webpush(
                subscription_info=sub["subscription"],
                data=json.dumps({"title": title, "body": body, "url": url}),
                vapid_private_key=pem_path,
                vapid_claims=dict(VAPID_CLAIMS),
            )
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):  # subscription expired or revoked
                database.delete_push_subscription(sub["endpoint"])
            else:
                logging.warning("push failed for user %s: %s", user_id, e)
        except Exception as e:
            logging.warning("push failed for user %s: %s", user_id, e)
    return sent
