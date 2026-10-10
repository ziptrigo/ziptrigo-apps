import pytest


@pytest.fixture()
def sent_emails(monkeypatch):
    """Capture what the feedback emails hand to `send_email` instead of sending anything."""
    sent: list[dict] = []
    monkeypatch.setattr(
        'apps.feedback.services.emails.send_email', lambda **kwargs: sent.append(kwargs)
    )
    return sent
