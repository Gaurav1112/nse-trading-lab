from __future__ import annotations
import json
import logging
import os
from pywebpush import webpush, WebPushException

log = logging.getLogger(__name__)


def send_push(payload: dict, subscriptions: list[dict]) -> list[str]:
    """Send a push to every subscription. Returns list of failed endpoints
    (410 Gone or auth errors — caller should prune them from storage)."""
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
        return [s["endpoint"] for s in subscriptions]

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
