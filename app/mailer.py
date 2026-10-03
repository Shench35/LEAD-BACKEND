import asyncio
import hashlib
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from typing import Any
from urllib.parse import urlsplit

from app.config import Settings

logger = logging.getLogger(__name__)
EMAIL_SUBJECT = "Your SIWES leads from Lead"
SAFETY_NOTICE = (
    "These are automated search leads, not confirmed vacancies. Verify every opportunity "
    "before applying, and never pay anyone for a placement."
)
FOOTER = (
    "You received this because this address was entered on Lead. If that was not you, "
    "ignore this email. Lead does not store your address."
)


def hash_email(email: str, salt: str) -> str:
    return hashlib.sha256((salt + email.strip().lower()).encode("utf-8")).hexdigest()


def mask_email(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator:
        return "***"
    return f"{local[:1]}***@{domain}"


def compose_email(
    role: str, location: str, results: list[dict[str, Any]]
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = EMAIL_SUBJECT

    text_lines = ["Hello,", "", f"Here are SIWES and internship leads for {role} in {location}.", ""]
    html_leads: list[str] = []
    for number, result in enumerate(results, start=1):
        title = str(result.get("title", ""))
        link = str(result.get("link", ""))
        reason = str(result.get("reason", ""))
        tip = str(result.get("tip", ""))
        text_lines.extend(
            [f"{number}. {title}", link, f"Why it may fit: {reason}", f"Tip: {tip}", ""]
        )
        safe_title = escape(title)
        parsed_link = urlsplit(link)
        if parsed_link.scheme in {"http", "https"} and parsed_link.netloc:
            title_html = f'<a href="{escape(link, quote=True)}">{safe_title}</a>'
        else:
            title_html = safe_title
        html_leads.append(
            f"<li><strong>{title_html}</strong><br>"
            f"{escape(link)}<br>Why it may fit: {escape(reason)}<br>Tip: {escape(tip)}</li>"
        )

    text_lines.extend([SAFETY_NOTICE, "", FOOTER])
    html_body = (
        f"<p>Hello,</p><p>Here are SIWES and internship leads for {escape(role)} in "
        f"{escape(location)}.</p><ol>{''.join(html_leads)}</ol>"
        f"<p>{escape(SAFETY_NOTICE)}</p><p>{escape(FOOTER)}</p>"
    )
    message.set_content("\n".join(text_lines))
    message.add_alternative(html_body, subtype="html")
    return message


def _send_sync(email: str, message: EmailMessage, settings: Settings) -> None:
    if not settings.smtp_host or not settings.smtp_port or not settings.smtp_user:
        raise RuntimeError("Email delivery is not fully configured.")
    message["From"] = formataddr((settings.email_from_name, settings.smtp_user))
    message["To"] = email
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        if settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


async def send_email(
    email: str,
    role: str,
    location: str,
    results: list[dict[str, Any]],
    settings: Settings,
) -> bool:
    message = compose_email(role, location, results)
    try:
        await asyncio.to_thread(_send_sync, email, message, settings)
        return True
    except Exception:
        logger.warning("Email send failed for %s", mask_email(email))
        return False
