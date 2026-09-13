"""Email delivery for password-reset codes.

Uses plain SMTP with STARTTLS (works with Brevo, Gmail app-passwords, Zoho,
Mailgun relay, etc.). Configure via env:

    SMTP_HOST=smtp-relay.brevo.com
    SMTP_PORT=587
    SMTP_USER=your-login
    SMTP_PASSWORD=your-key
    SMTP_FROM="ClutchD <no-reply@yourdomain>"
    SMTP_TLS=true

If SMTP_HOST is unset, sending is skipped and the caller surfaces the code
through the dev-mode fallback (see auth.py) so the flow never becomes
impossible to complete.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import get_settings

logger = logging.getLogger(__name__)

RESET_TEMPLATE = """\
Hi {name},

Your ClutchD password reset code is:

    {code}

This code expires in 10 minutes. If you didn't request a reset,
you can safely ignore this email — your password stays unchanged.

— The ClutchD Team
"""

RESET_HTML = """\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
  <h2 style="color:#1E29B6;margin:0 0 4px;">ClutchD</h2>
  <p style="color:#555;">Hi {name},</p>
  <p style="color:#333;">Your password reset code is:</p>
  <p style="font-size:28px;font-weight:bold;letter-spacing:4px;color:#1E29B6;
     background:#f4f5ff;padding:12px 18px;border-radius:10px;text-align:center;">{code}</p>
  <p style="color:#555;">This code expires in <b>10 minutes</b>.</p>
  <p style="color:#999;font-size:13px;">If you didn't request a reset, you can safely
  ignore this email — your password stays unchanged.</p>
</div>
"""


def send_reset_email(*, to_email: str, name: str, code: str) -> bool:
    """Send the reset code by email. Returns True when actually sent.

    Never raises — failures are logged and reported as False so auth flows
    can fall back gracefully.
    """
    settings = get_settings()
    if not settings.smtp_host:
        logger.warning("SMTP_HOST not set — reset code for %s not emailed", to_email)
        return False

    safe_name = (name or "there").split("@")[0][:40]
    subject = f"Your ClutchD password reset code: {code}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or "ClutchD <no-reply@clutchd.app>"
    msg["To"] = to_email
    msg.attach(MIMEText(RESET_TEMPLATE.format(name=safe_name, code=code), "plain"))
    msg.attach(MIMEText(RESET_HTML.format(name=safe_name, code=code), "html"))

    try:
        port = settings.smtp_port or 587
        use_ssl = (settings.smtp_tls or "true").lower() in ("1", "true", "yes")
        if port == 465 and use_ssl:
            with smtplib.SMTP_SSL(settings.smtp_host, port, timeout=15) as smtp:
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.sendmail(msg["From"], [to_email], msg.as_string())
        else:
            with smtplib.SMTP(settings.smtp_host, port, timeout=15) as smtp:
                smtp.ehlo()
                if use_ssl:
                    smtp.starttls()
                    smtp.ehlo()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.sendmail(msg["From"], [to_email], msg.as_string())
        logger.info("Reset email sent to %s", to_email)
        return True
    except Exception as exc:  # noqa: BLE001 — any SMTP failure must not break auth
        logger.error("Failed to send reset email to %s: %s", to_email, exc)
        return False
