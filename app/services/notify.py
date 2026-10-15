"""
Tells the owner about new contact messages by email (Gmail SMTP with an app
password). The email carries the whole message, so it can be read and
answered from the inbox (replying answers the visitor directly); the admin's
Messages page lists them too.
"""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr

from loguru import logger
from prometheus_client import Counter

from core import config

NOTIFICATIONS = Counter(
    "contact_notifications_total",
    "Emails about new contact messages, by result.",
    ["result"],
)
# Start at 0, so increase() sees the first failure (see services.rate_limit).
for _result in ("sent", "failed"):
    NOTIFICATIONS.labels(_result)


@dataclass(frozen=True)
class NewMessage:
    name: str
    email: str
    subject: str
    message: str


def enabled() -> bool:
    return bool(
        config.SMTP_USERNAME and str(config.SMTP_PASSWORD) and config.CONTACT_NOTIFY_TO
    )


def one_line(text: str) -> str:
    # Visitor text goes into headers: a line break there could add headers
    # of its own (header injection).
    return " ".join(text.split())


def build_email(new: NewMessage) -> EmailMessage:
    email = EmailMessage()
    email["From"] = formataddr(("yarikama.com", config.SMTP_USERNAME))
    email["To"] = config.CONTACT_NOTIFY_TO
    email["Reply-To"] = formataddr((one_line(new.name), one_line(new.email)))
    email["Subject"] = f"[yarikama.com] {one_line(new.subject)}"
    email.set_content(
        f"From: {new.name} <{new.email}>\n"
        f"Subject: {new.subject}\n"
        f"\n{new.message}\n"
        f"\n--\nSent from the contact form on yarikama.com. "
        f"Reply to this email to answer {new.name}.\n"
    )
    return email


def send_contact_notification(new: NewMessage) -> None:
    """Runs after the response is sent; never raises, the message is saved."""
    if not enabled():
        return
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USERNAME, str(config.SMTP_PASSWORD))
            smtp.send_message(build_email(new))
    except (OSError, smtplib.SMTPException, ValueError) as error:
        NOTIFICATIONS.labels("failed").inc()
        logger.error(f"Contact notification failed: {error!r}")
        return
    NOTIFICATIONS.labels("sent").inc()
