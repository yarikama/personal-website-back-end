import smtplib
import uuid

import pytest
from core import config
from db.dependency import get_db
from fastapi.testclient import TestClient
from main import get_application
from services import notify
from services.notify import NewMessage, build_email, send_contact_notification
from starlette.datastructures import Secret

MESSAGE = NewMessage(
    name="Ada Lovelace",
    email="ada@example.com",
    subject="Engines",
    message="Could we talk about\nthe Analytical Engine?",
)


class FakeSMTP:
    sent: list = []
    fail = False

    def __init__(self, host, port, timeout):
        self.address = (host, port)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        if FakeSMTP.fail:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.credentials = (user, password)

    def send_message(self, email):
        FakeSMTP.sent.append((self.address, self.credentials, email))


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.fail = False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(config, "SMTP_USERNAME", "site@example.com")
    monkeypatch.setattr(config, "SMTP_PASSWORD", Secret("app-password"))
    monkeypatch.setattr(config, "CONTACT_NOTIFY_TO", "owner@example.com")
    return FakeSMTP


def sent_total(result):
    return notify.NOTIFICATIONS.labels(result)._value.get()


def test_the_email_carries_the_message_and_replies_go_to_the_visitor(smtp):
    email = build_email(MESSAGE)

    assert email["To"] == "owner@example.com"
    assert email["From"] == '"yarikama.com" <site@example.com>'
    assert email["Reply-To"] == "Ada Lovelace <ada@example.com>"
    assert email["Subject"] == "[yarikama.com] Engines"
    assert "the Analytical Engine?" in email.get_content()


def test_line_breaks_cannot_add_headers(smtp):
    email = build_email(
        NewMessage(
            "Eve\r\nBcc: victim@example.com",
            "eve@example.com",
            "Hi\nBcc: victim@example.com",
            "body",
        )
    )

    assert email["Bcc"] is None
    assert email["Subject"] == "[yarikama.com] Hi Bcc: victim@example.com"


def test_sends_over_smtp_with_the_configured_account(smtp):
    before = sent_total("sent")

    send_contact_notification(MESSAGE)

    [(address, credentials, email)] = smtp.sent
    assert address == ("smtp.gmail.com", 587)
    assert credentials == ("site@example.com", "app-password")
    assert sent_total("sent") == before + 1


def test_a_failure_is_counted_not_raised(smtp):
    smtp.fail = True
    before = sent_total("failed")

    send_contact_notification(MESSAGE)

    assert smtp.sent == []
    assert sent_total("failed") == before + 1


def test_nothing_is_sent_when_not_configured(smtp, monkeypatch):
    monkeypatch.setattr(config, "CONTACT_NOTIFY_TO", "")

    send_contact_notification(MESSAGE)

    assert smtp.sent == []


class FakeSession:
    def add(self, contact):
        self.contact = contact

    async def commit(self):
        self.contact.id = uuid.uuid4()

    async def refresh(self, contact):
        pass


def test_submitting_the_form_sends_the_email_after_saving(smtp):
    app = get_application()
    app.dependency_overrides[get_db] = FakeSession

    response = TestClient(app).post(
        "/api/v1/contact",
        json={
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "subject": "Engines",
            "message": "Could we talk about the Analytical Engine?",
        },
    )

    assert response.status_code == 201
    [(_, _, email)] = smtp.sent
    assert email["Reply-To"] == "Ada Lovelace <ada@example.com>"


def test_the_form_still_works_when_gmail_fails(smtp):
    smtp.fail = True
    app = get_application()
    app.dependency_overrides[get_db] = FakeSession

    response = TestClient(app).post(
        "/api/v1/contact",
        json={
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "subject": "Engines",
            "message": "Could we talk about the Analytical Engine?",
        },
    )

    assert response.status_code == 201
