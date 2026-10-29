from __future__ import annotations
import json
import logging
import os

log = logging.getLogger(__name__)

try:  # pywebpush is optional: only needed when a push is actually sent
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None

    class WebPushException(Exception):  # placeholder so the except clause stays valid
        pass


class PushDisabled(RuntimeError):
    """Push cannot be sent at all (pywebpush missing or no VAPID key). Nothing was attempted,
    so no endpoint is known to be dead: callers must NOT prune subscriptions on this error."""


def send_push(payload: dict, subscriptions: list[dict]) -> list[str]:
    """Send a push to every subscription. Returns list of failed endpoints
    (410 Gone or auth errors — caller may prune them from storage).

    Raises PushDisabled when pushing is impossible for every endpoint (missing package or
    VAPID key); that is a configuration problem, never a reason to delete subscriptions."""
    priv = os.environ.get("VAPID_PRIVATE_KEY_PEM")
    contact = os.environ.get("VAPID_CONTACT", "mailto:nobody@example.com")
    if priv is None:
        # Fallback for local dev — read from file path
        priv_path = os.environ.get("VAPID_PRIVATE_KEY_PATH")
        if priv_path:
            with open(priv_path) as f:
                priv = f.read()
    if priv is None:
        log.error("No VAPID private key configured — push disabled")
        raise PushDisabled("no VAPID private key configured")

    if webpush is None:
        log.error("pywebpush is not installed — pip install pywebpush; push disabled")
        raise PushDisabled("pywebpush not installed")

    failed: list[str] = []
    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps(payload),
                vapid_private_key=priv,
                vapid_claims={"sub": contact},
                ttl=300,
            )
        except (WebPushException, RuntimeError) as e:
            log.warning("Push failed for %s: %s", sub["endpoint"][:40], e)
            failed.append(sub["endpoint"])
    return failed
