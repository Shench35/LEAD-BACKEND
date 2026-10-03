import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app import db
from app.config import Settings
from app.mailer import EMAIL_SUBJECT, compose_email, hash_email, mask_email, send_email


def mail_settings():
    return Settings(
        lead_access_key="test",
        email_enabled=True,
        email_hash_salt="test-salt",
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_user="lead@example.test",
        smtp_password="smtp-password",
    )


def test_message_has_plain_and_escaped_html_alternatives():
    message = compose_email(
        "<script>role</script>",
        'Lagos "Island"',
        [{
            "title": "Training <position>",
            "link": "https://jobs.example.test/apply?a=1&b=2",
            "reason": "Relevant & current",
            "tip": "Check <details>",
        }],
    )

    assert message["Subject"] == EMAIL_SUBJECT
    assert len(message.get_payload()) == 2
    html_body = message.get_payload()[1].get_content()
    assert "&lt;script&gt;role&lt;/script&gt;" in html_body
    assert "Lagos &quot;Island&quot;" in html_body
    assert "Training &lt;position&gt;" in html_body
    assert "&amp;b=2" in html_body
    assert "never pay anyone for a placement" in html_body
    assert "Lead does not store your address." in html_body


def test_send_uses_starttls_executor_and_logs_only_masked_address(monkeypatch, caplog):
    sent = {}
    main_thread = threading.get_ident()

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"] = host
            sent["port"] = port
            sent["timeout"] = timeout

        def __enter__(self):
            sent["thread"] = threading.get_ident()
            return self

        def __exit__(self, *args):
            return None

        def ehlo(self):
            return None

        def starttls(self, *, context):
            sent["starttls"] = context is not None

        def login(self, user, password):
            sent["login"] = (user, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr("app.mailer.smtplib.SMTP", FakeSMTP)
    email = "student@gmail.com"
    success = asyncio.run(
        send_email(
            email,
            "Software",
            "Lagos",
            [{"title": "SIWES", "link": "https://jobs.example.test", "reason": "Fit", "tip": "Apply"}],
            mail_settings(),
        )
    )

    assert success is True
    assert sent["timeout"] == 20
    assert sent["starttls"] is True
    assert sent["thread"] != main_thread
    assert sent["message"]["To"] == email
    assert sent["message"]["From"] == "Lead <lead@example.test>"

    class FailingSMTP(FakeSMTP):
        def starttls(self, *, context):
            raise RuntimeError(f"simulated error for {email}")

    monkeypatch.setattr("app.mailer.smtplib.SMTP", FailingSMTP)
    success = asyncio.run(
        send_email(email, "Software", "Lagos", [], mail_settings())
    )
    assert success is False
    assert email not in caplog.text
    assert mask_email(email) in caplog.text


def test_email_enabled_requires_hash_salt():
    with pytest.raises(ValidationError):
        Settings(lead_access_key="test", email_enabled=True)


def test_email_send_log_purges_after_48_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "mail.sqlite3")
    db.initialize_database()
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    old_hash = hash_email("old@example.com", "salt")
    recent_hash = hash_email("recent@example.com", "salt")
    db.record_email_send(old_hash, sent_at=now - timedelta(hours=49))
    db.record_email_send(recent_hash, sent_at=now - timedelta(hours=2))

    db.purge_expired_email_sends(now=now)

    assert db.email_send_count(old_hash, now=now) == 0
    assert db.email_send_count(recent_hash, now=now) == 1
