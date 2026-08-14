"""APNs (native iOS) push — token-based (.p8) provider auth over HTTP/2.

A parallel delivery channel to web push so the native BuiltHere app receives the
same notifications. Configure by dropping the APNs auth key at data/apns_key.p8
and its Key ID (env APNS_KEY_ID or data/apns.json). Team/bundle default to the
BuiltHere values. If unconfigured, every call is a graceful no-op.

Requires: pip install "httpx[http2]" "pyjwt[crypto]"
"""
import json
import logging
import os
import time
from pathlib import Path

import database

DATA_DIR = Path(__file__).parent / "data"
KEY_PATH = Path(os.environ.get("APNS_KEY_PATH", str(DATA_DIR / "apns_key.p8")))
_CFG_PATH = DATA_DIR / "apns.json"

_HOSTS = {
    "sandbox": "https://api.sandbox.push.apple.com",
    "production": "https://api.push.apple.com",
}


def _cfg():
    c = {}
    if _CFG_PATH.exists():
        try:
            c = json.loads(_CFG_PATH.read_text())
        except Exception:
            c = {}
    return {
        "key_id": os.environ.get("APNS_KEY_ID") or c.get("key_id"),
        "team_id": os.environ.get("APNS_TEAM_ID") or c.get("team_id") or "65UXVW2M34",
        "bundle_id": os.environ.get("APNS_BUNDLE_ID") or c.get("bundle_id") or "com.homefit.app",
    }


def is_available():
    """True once the .p8 key + Key ID are in place."""
    return KEY_PATH.exists() and bool(_cfg().get("key_id"))


_jwt_cache = {"token": None, "ts": 0}


def _provider_token():
    """Signed ES256 provider JWT. APNs allows reuse up to 1h; refresh every 40m."""
    cfg = _cfg()
    now = int(time.time())
    if _jwt_cache["token"] and now - _jwt_cache["ts"] < 2400:
        return _jwt_cache["token"]
    import jwt  # PyJWT[crypto]
    tok = jwt.encode({"iss": cfg["team_id"], "iat": now}, KEY_PATH.read_text(),
                     algorithm="ES256", headers={"kid": cfg["key_id"]})
    _jwt_cache.update(token=tok, ts=now)
    return tok


def send_to_user_apns(user_id, title, body, url="/"):
    """Push to every registered iOS device for a user. Prunes dead tokens.
    Returns the number of successful sends (0 if APNs isn't configured)."""
    if not is_available():
        return 0
    tokens = database.get_apns_tokens(user_id)
    if not tokens:
        return 0
    import httpx
    cfg = _cfg()
    bearer = _provider_token()
    payload = json.dumps({
        "aps": {"alert": {"title": title, "body": body}, "sound": "default"},
        "url": url,
    }).encode()
    sent = 0
    with httpx.Client(http2=True, timeout=10) as client:
        for t in tokens:
            host = _HOSTS.get(t.get("environment") or "production", _HOSTS["production"])
            try:
                r = client.post(
                    f"{host}/3/device/{t['device_token']}", content=payload,
                    headers={"authorization": f"bearer {bearer}",
                             "apns-topic": cfg["bundle_id"], "apns-push-type": "alert"})
                if r.status_code == 200:
                    sent += 1
                elif r.status_code == 410 or (r.status_code == 400 and "BadDeviceToken" in r.text):
                    database.delete_apns_token(t["device_token"])  # device no longer valid
                else:
                    logging.warning("APNs %s for user %s: %s", r.status_code, user_id, r.text[:200])
            except Exception as e:
                logging.warning("APNs send failed for user %s: %s", user_id, e)
    return sent
