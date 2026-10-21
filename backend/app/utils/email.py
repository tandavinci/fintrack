"""Async email utility for sending emails via SMTP.

Provides async email sending using smtplib in a thread executor,
HTML templates for verification, password reset, and alert notifications,
and retry handling via Celery for delivery failures.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import partial

from backend.app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core email sending
# ---------------------------------------------------------------------------


async def send_email(to: str, subject: str, html_content: str) -> bool:
    """Send an email asynchronously via SMTP.

    Uses smtplib executed in a thread pool executor to avoid blocking
    the async event loop.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        html_content: HTML body content.

    Returns:
        True if the email was sent successfully, False otherwise.
    """
    import asyncio

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None, partial(_send_email_sync, to, subject, html_content)
        )
        logger.info("Email sent successfully to %s (subject: %s)", to, subject)
        return True
    except Exception as exc:
        logger.error(
            "Failed to send email to %s (subject: %s): %s", to, subject, exc
        )
        # Queue for retry via Celery
        _queue_email_retry(to, subject, html_content)
        return False


def _send_email_sync(to: str, subject: str, html_content: str) -> None:
    """Synchronous SMTP email sending (run in executor).

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


def _queue_email_retry(to: str, subject: str, html_content: str) -> None:
    """Queue a failed email for retry via Celery.

    Attempts to dispatch the email as a Celery task with exponential backoff.
    If queueing itself fails (e.g., Redis unavailable), the failure is logged.
    """
    try:
        from backend.app.tasks.email_tasks import send_email_task

        send_email_task.apply_async(
            args=[to, subject, html_content],
            countdown=60,  # First retry after 60 seconds
            max_retries=3,
        )
        logger.info("Email queued for retry: to=%s, subject=%s", to, subject)
    except Exception as exc:
        logger.error(
            "Failed to queue email retry for %s: %s. Email will not be delivered.",
            to,
            exc,
        )


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------


def _base_template(title: str, body_content: str) -> str:
    """Wrap body content in a consistent HTML email template.

    Args:
        title: Email title (used in HTML head).
        body_content: Inner HTML content for the email body.

    Returns:
        Complete HTML string for the email.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background-color: #ffffff;
            border-radius: 8px;
            padding: 32px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }}
        .header {{
            text-align: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 2px solid #e0e0e0;
        }}
        .header h1 {{
            color: #1a237e;
            margin: 0;
            font-size: 24px;
        }}
        .btn {{
            display: inline-block;
            padding: 12px 24px;
            background-color: #1a237e;
            color: #ffffff;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 600;
            margin: 16px 0;
        }}
        .footer {{
            text-align: center;
            font-size: 12px;
            color: #999;
            margin-top: 24px;
            padding-top: 16px;
            border-top: 1px solid #e0e0e0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{settings.app_name}</h1>
        </div>
        {body_content}
        <div class="footer">
            <p>This is an automated message from {settings.app_name}.</p>
            <p>Please do not reply to this email.</p>
        </div>
    </div>
</body>
</html>"""


def _render_verification_email(user_name: str, verification_url: str) -> str:
    """Render the email verification HTML template.

    Args:
        user_name: User's display name.
        verification_url: The full URL to verify the email.

    Returns:
        Complete HTML email string.
    """
    body = f"""
        <h2>Welcome, {user_name}!</h2>
        <p>Thank you for registering with {settings.app_name}. Please verify your
        email address to activate your account and start tracking your investments.</p>
        <p style="text-align: center;">
            <a href="{verification_url}" class="btn">Verify Email Address</a>
        </p>
        <p>If the button doesn't work, copy and paste this link into your browser:</p>
        <p style="word-break: break-all; font-size: 14px; color: #666;">{verification_url}</p>
        <p>This link expires in 24 hours.</p>
    """
    return _base_template("Verify Your Email", body)


def _render_password_reset_email(user_name: str, reset_url: str) -> str:
    """Render the password reset HTML template.

    Args:
        user_name: User's display name.
        reset_url: The full URL to reset the password.

    Returns:
        Complete HTML email string.
    """
    body = f"""
        <h2>Password Reset Request</h2>
        <p>Hello {user_name},</p>
        <p>We received a request to reset the password for your account. Click the
        button below to set a new password:</p>
        <p style="text-align: center;">
            <a href="{reset_url}" class="btn">Reset Password</a>
        </p>
        <p>If the button doesn't work, copy and paste this link into your browser:</p>
        <p style="word-break: break-all; font-size: 14px; color: #666;">{reset_url}</p>
        <p>This link expires in 1 hour. If you did not request a password reset,
        you can safely ignore this email.</p>
    """
    return _base_template("Reset Your Password", body)


def _render_alert_notification_email(
    user_name: str,
    stock_name: str,
    alert_type: str,
    threshold_value: str,
    current_value: str,
) -> str:
    """Render the alert notification HTML template.

    Args:
        user_name: User's display name.
        stock_name: The stock's company name or ticker.
        alert_type: The alert type (e.g., PRICE_ABOVE, TREND_BELOW).
        threshold_value: The threshold that was crossed.
        current_value: The current price or trend score.

    Returns:
        Complete HTML email string.
    """
    alert_type_display = alert_type.replace("_", " ").title()
    body = f"""
        <h2>Alert Triggered</h2>
        <p>Hello {user_name},</p>
        <p>Your alert for <strong>{stock_name}</strong> has been triggered:</p>
        <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
            <tr style="background-color: #f5f5f5;">
                <td style="padding: 12px; border: 1px solid #e0e0e0; font-weight: 600;">Alert Type</td>
                <td style="padding: 12px; border: 1px solid #e0e0e0;">{alert_type_display}</td>
            </tr>
            <tr>
                <td style="padding: 12px; border: 1px solid #e0e0e0; font-weight: 600;">Threshold</td>
                <td style="padding: 12px; border: 1px solid #e0e0e0;">{threshold_value}</td>
            </tr>
            <tr style="background-color: #f5f5f5;">
                <td style="padding: 12px; border: 1px solid #e0e0e0; font-weight: 600;">Current Value</td>
                <td style="padding: 12px; border: 1px solid #e0e0e0;">{current_value}</td>
            </tr>
        </table>
        <p>Log in to your account to review your alerts and take action.</p>
    """
    return _base_template("Alert Notification", body)


# ---------------------------------------------------------------------------
# Public template helper functions
# ---------------------------------------------------------------------------


async def send_verification_email(
    to: str, user_name: str, verification_token: str
) -> bool:
    """Send an email verification message to a new user.

    Constructs the verification URL and sends the formatted email.

    Args:
        to: Recipient email address.
        user_name: User's display name for personalization.
        verification_token: The token to include in the verification URL.

    Returns:
        True if sent successfully, False otherwise (queued for retry).
    """
    # Construct verification URL (frontend route)
    verification_url = f"{settings.allowed_origins[0]}/verify-email/{verification_token}"
    html_content = _render_verification_email(user_name, verification_url)
    return await send_email(to, "Verify Your Email Address", html_content)


async def send_password_reset_email(
    to: str, user_name: str, reset_token: str
) -> bool:
    """Send a password reset email with a secure reset link.

    Args:
        to: Recipient email address.
        user_name: User's display name for personalization.
        reset_token: The password reset token.

    Returns:
        True if sent successfully, False otherwise (queued for retry).
    """
    reset_url = f"{settings.allowed_origins[0]}/reset-password/{reset_token}"
    html_content = _render_password_reset_email(user_name, reset_url)
    return await send_email(to, "Reset Your Password", html_content)


async def send_alert_notification(
    to: str,
    user_name: str,
    stock_name: str,
    alert_type: str,
    threshold_value: str,
    current_value: str,
) -> bool:
    """Send an alert notification email when an alert condition is triggered.

    Args:
        to: Recipient email address.
        user_name: User's display name for personalization.
        stock_name: Name or ticker of the stock that triggered the alert.
        alert_type: Type of alert (PRICE_ABOVE, PRICE_BELOW, TREND_ABOVE, TREND_BELOW).
        threshold_value: The configured threshold value.
        current_value: The current value that triggered the alert.

    Returns:
        True if sent successfully, False otherwise (queued for retry).
    """
    html_content = _render_alert_notification_email(
        user_name, stock_name, alert_type, threshold_value, current_value
    )
    subject = f"Alert: {stock_name} - {alert_type.replace('_', ' ').title()}"
    return await send_email(to, subject, html_content)
