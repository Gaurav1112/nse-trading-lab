from unittest.mock import patch
from pipeline.push import send_push


def test_send_push_returns_empty_on_all_success():
    subs = [{"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "x", "auth": "y"}}]
    with patch("pipeline.push.webpush") as mock_wp:
        with patch.dict("os.environ", {"VAPID_PRIVATE_KEY_PEM": "test-key"}):
            mock_wp.return_value = None
            failed = send_push({"title": "T", "body": "x"}, subs)
    assert failed == []


def test_send_push_returns_failed_endpoints():
    subs = [{"endpoint": "https://fcm.googleapis.com/fcm/send/dead", "keys": {"p256dh": "x", "auth": "y"}}]
    with patch("pipeline.push.webpush", side_effect=RuntimeError("410 Gone")):
        with patch.dict("os.environ", {"VAPID_PRIVATE_KEY_PEM": "test-key"}):
            failed = send_push({"title": "T", "body": "x"}, subs)
    assert failed == ["https://fcm.googleapis.com/fcm/send/dead"]
