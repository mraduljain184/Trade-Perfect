"""E-mail outbox (PROJECT_CONTEXT section 6.2 / FR-AT).

`queue_email` only writes an email_outbox row (in the caller's transaction). `send_pending` runs
later as a background task with its OWN session and sends the PENDING rows over SMTP.
Without SMTP_HOST nothing is sent: rows are stored as LOGGED_ONLY and the report file is kept.
E-mail problems are logged and stored on the row - they never fail a request.
"""
import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import EmailOutbox, User

logger = logging.getLogger("app.email")

SMTP_TIMEOUT_SECONDS = 20
_TYPES = {".pdf": ("application", "pdf"), ".csv": ("text", "csv")}


def smtp_ready() -> bool:
    return bool(get_settings().smtp_host)


def queue_email(db: Session, to_addresses: list[str], subject: str, body: str, purpose: str,
                attachment_path: str | None = None, created_by: User | None = None) -> EmailOutbox:
    """Add one outbox row. Status PENDING when SMTP is configured, else LOGGED_ONLY."""
    row = EmailOutbox(
        to_addresses=", ".join(to_addresses), subject=subject[:255], body=body, purpose=purpose,
        attachment_path=attachment_path, status="PENDING" if smtp_ready() else "LOGGED_ONLY",
        created_by=created_by.id if created_by else None)
    db.add(row)
    if row.status == "LOGGED_ONLY":
        logger.info("E-mail not sent (no SMTP_HOST): %s -> %s", row.subject, row.to_addresses)
    return row


def _attachment(path_text: str | None) -> Path | None:
    """Attachments are always files in the reports folder; only the file name is trusted."""
    if not path_text:
        return None
    from app.services import upload_service  # local import: keeps this module free of service cycles

    path = upload_service.REPORT_DIR / Path(path_text).name
    return path if path.is_file() else None


def _send(row: EmailOutbox) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = settings.smtp_from, row.to_addresses, row.subject
    message.set_content(row.body)
    attachment = _attachment(row.attachment_path)
    if attachment:
        main_type, sub_type = _TYPES.get(attachment.suffix.lower(), ("application", "octet-stream"))
        message.add_attachment(attachment.read_bytes(), maintype=main_type, subtype=sub_type, filename=attachment.name)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.ehlo()
        if smtp.has_extn("starttls"):
            smtp.starttls()
            smtp.ehlo()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


def send_pending() -> int:
    """Background task: send every PENDING row. Returns how many were sent. Never raises."""
    if not smtp_ready():
        return 0
    sent = 0
    try:
        with SessionLocal() as db:
            rows = db.scalars(select(EmailOutbox).where(EmailOutbox.status == "PENDING")
                              .order_by(EmailOutbox.id).with_for_update(skip_locked=True)).all()
            for row in rows:
                try:
                    _send(row)
                    row.status, row.sent_at, row.error_message = "SENT", datetime.now(timezone.utc), None
                    sent += 1
                except Exception as exc:  # noqa: BLE001 - one bad address must not stop the others
                    logger.warning("E-mail %s could not be sent: %s", row.id, exc)
                    row.status, row.error_message = "FAILED", str(exc)[:2000]
            db.commit()
    except Exception:  # noqa: BLE001 - a background task has nobody to report to
        logger.exception("E-mail outbox run failed")
    return sent
