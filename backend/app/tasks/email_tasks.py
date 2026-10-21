"""Celery tasks for email delivery with retry support.

Handles queued email retries with exponential backoff when initial
SMTP delivery fails.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from celery import shared_task

from backend.app.config import settings

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="app.tasks.email_tasks.send_email_task",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(smtplib.SMTPException, ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=300,
)
def send_email_task(self, to: str, subject: str, html_content: str) -> bool:
    """Celery task for sending an email with automatic retry on failure.

    Uses exponential backoff: retry 1 at ~60s, retry 2 at ~120s, retry 3 at ~240s.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        html_content: HTML body content.

    Returns:
        True if the email was sent successfully.

    Raises:
        smtplib.SMTPException: If all retries are exhausted.
    """
    try:
        _send_email_sync(to, subject, html_content)
        logger.info(
            "Email delivered successfully via retry task: to=%s, subject=%s (attempt %d)",
            to,
            subject,
            self.request.retries + 1,
        )
        return True
    except (smtplib.SMTPException, ConnectionError, TimeoutError, OSError) as exc:
        logger.warning(
            "Email delivery attempt %d failed for %s: %s",
            self.request.retries + 1,
            to,
            exc,
        )
        raise  # Let Celery's autoretry handle it


def _send_email_sync(to: str, subject: str, html_content: str) -> None:
    """Synchronous SMTP email sending for Celery worker context.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        html_content: HTML body content.

    Raises:
        smtplib.SMTPException: On any SMTP-related failure.
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = settings.smtp_from_email
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html_content, "html"))

    if settings.smtp_use_tls:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)

    try:
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.sendmail(settings.smtp_from_email, [to], msg.as_string())
    finally:
        server.quit()
