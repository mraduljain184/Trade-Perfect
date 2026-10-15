"""PDF audit report for one trade (endpoints 34-35, FR-AT).

Content: trade details, audit box, the full audit timeline, the hash-chain check, who generated it and when.
The file is saved in the reports folder; an e-mail with the PDF attached is put in the outbox.
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.models import AuditLog, User
from app.schemas.audit import ReportOut
from app.schemas.trade import TradeDetail
from app.services import audit_query_service, audit_service, email_service, engine_adapter, trade_service, upload_service

logger = logging.getLogger("app.reports")

FILE_NAME = re.compile(r"^audit_[A-Za-z0-9_-]{1,40}_\d{8}_\d{9}\.pdf$")
PAGE_COMPRESSION = 1  # tests switch this off to read the text inside the PDF
BRAND = colors.HexColor("#0b5cad")
_STYLES = getSampleStyleSheet()
_CELL = ParagraphStyle("cell", parent=_STYLES["BodyText"], fontSize=8, leading=10)
_HEAD = ParagraphStyle("head", parent=_CELL, textColor=colors.white, fontName="Helvetica-Bold")
_TABLE = TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), BRAND), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d1dc")),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f9")]),
    ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
])
_PAIRS = TableStyle([
    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f7")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8d1dc")),
    ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
])


def _p(value, style=_CELL) -> Paragraph:
    return Paragraph(escape("" if value is None else str(value)), style)


def _local(moment: datetime | None) -> str:
    if moment is None:
        return ""
    return moment.astimezone(ZoneInfo(get_settings().app_timezone)).strftime("%d %b %Y %H:%M:%S")


def _pairs(rows: list[tuple[str, object]]) -> Table:
    table = Table([[_p(label), _p(value)] for label, value in rows], colWidths=[45 * mm, 222 * mm])
    table.setStyle(_PAIRS)
    return table


def _event_note(event: AuditLog) -> str:
    parts = []
    if event.applied_fix:
        parts.append(f"Fix: {event.applied_fix}")
    details = event.details or {}
    for key in ("reason", "note", "changeReason", "assignee"):
        if details.get(key):
            parts.append(f"{key}: {details[key]}")
    if details.get("changedFields"):
        parts.append("changed: " + ", ".join(details["changedFields"]))
    if not parts and event.likely_reason:
        parts.append(event.likely_reason)
    return "; ".join(parts)


def build_pdf(path: Path, trade: TradeDetail, events: list[AuditLog], chain: tuple[bool, int | None],
              generated_by: User, generated_at: datetime) -> None:
    document = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=15 * mm, rightMargin=15 * mm,
                                 topMargin=14 * mm, bottomMargin=14 * mm, pageCompression=PAGE_COMPRESSION,
                                 title=f"Audit report {trade.trade_id}", author="Trade Perfect")
    box = trade.audit_box
    story = [
        Paragraph("Trade Perfect - Audit Report", _STYLES["Title"]),
        Paragraph(f"Trade {escape(str(trade.trade_id))}", _STYLES["Heading2"]),
        _pairs([
            ("Trade", f"{trade.trade_type} {trade.quantity:,} {trade.symbol} @ {trade.price} with {trade.counterparty}"
             if trade.quantity is not None else trade.trade_id),
            ("Trade date / Settlement date", f"{trade.trade_date} / {trade.settlement_date}"),
            ("Account / Trader", f"{trade.account_id} / {trade.trader_name}"),
            ("SSI matched / Currency matched", f"{'YES' if trade.ssi_matched else 'NO'} / {'YES' if trade.currency_matched else 'NO'}"),
            ("Status / Priority / Version", f"{trade.status} / {trade.priority or '-'} / v{trade.version}"),
            ("Scores (final / rule / ML)", f"{trade.final_score:.4f} / "
             f"{'-' if trade.rule_score is None else format(trade.rule_score, '.4f')} / "
             f"{'-' if trade.ml_score is None else format(trade.ml_score, '.4f')}"),
            ("Explanation", trade.explanation),
        ]),
        Spacer(1, 5 * mm), Paragraph("Audit box", _STYLES["Heading3"]),
        _pairs([
            ("Trade Id", box.trade_id), ("Queue ID", box.queue_id), ("Risk Score", f"{box.risk_score:.4f}"),
            ("Likely Reason", box.likely_reason), ("Recommended Fix", box.recommended_fix),
            ("Applied Fix", box.applied_fix), ("Applied By", box.applied_by),
            ("Applied Timestamp", _local(box.applied_timestamp)), ("Status", box.status),
        ]),
        Spacer(1, 5 * mm), Paragraph(f"Timeline ({len(events)} events, oldest first)", _STYLES["Heading3"]),
    ]
    rows = [[_p(h, _HEAD) for h in ("#", "Time (IST)", "Action", "User (role)", "Status", "Score", "Details")]]
    for number, event in enumerate(events, start=1):
        rows.append([
            _p(number), _p(_local(event.event_time)), _p(event.action),
            _p(f"{event.actor_username} ({event.actor_role or '-'})"), _p(event.status_after),
            _p("" if event.risk_score is None else f"{event.risk_score:.4f}"), _p(_event_note(event)),
        ])
    timeline = Table(rows, repeatRows=1, colWidths=[9 * mm, 36 * mm, 44 * mm, 44 * mm, 24 * mm, 16 * mm, 94 * mm])
    timeline.setStyle(_TABLE)
    valid, broken_at = chain
    check = ("Audit hash chain verified: VALID - no audit row has been changed or removed." if valid
             else f"Audit hash chain verified: BROKEN at audit row #{broken_at} - the audit trail has been tampered with.")
    story += [
        timeline, Spacer(1, 5 * mm),
        Paragraph(escape(check), ParagraphStyle("check", parent=_STYLES["BodyText"],
                                                textColor=colors.HexColor("#237804" if valid else "#a8071a"))),
        Paragraph(escape(f"Generated by {generated_by.full_name} ({generated_by.username}) on {_local(generated_at)} IST"),
                  _STYLES["BodyText"]),
    ]
    document.build(story)


def generate(db: Session, trade_id: str, user: User, email_to: list[str] | None, ip_address: str | None) -> ReportOut:
    """Build the PDF, queue the e-mail and audit it - all in one transaction."""
    events = audit_query_service.timeline_rows(db, trade_id, user)  # 404 if the user may not see this trade
    trade = trade_service.get_trade(db, trade_id, user)
    now = datetime.now(timezone.utc)
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", trade_id)[:40]
    file_name = f"audit_{safe_id}_{now:%Y%m%d_%H%M%S}{now.microsecond // 1000:03d}.pdf"  # ms: two reports in one second must not collide
    folder = upload_service.REPORT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    build_pdf(folder / file_name, trade, events, audit_service.verify_chain(db), user, now)

    recipients = email_to or engine_adapter.get_setting(db, "audit_report_recipients", []) or []
    if isinstance(recipients, str):
        recipients = [recipients]
    email_status = "NOT_SENT"
    if recipients:
        row = email_service.queue_email(
            db, recipients, f"Trade Perfect: audit report for trade {trade_id}",
            f"Audit report for trade {trade_id} ({trade.status}, risk score {trade.final_score:.2f}), generated by "
            f"{user.full_name}.\nThe PDF with the full audit timeline is attached.",
            "AUDIT_REPORT", attachment_path=f"reports/{file_name}", created_by=user)
        email_status = row.status
    audit_service.log(db, "AUDIT_REPORT_GENERATED", "TRADE", actor=user, ip_address=ip_address,
                      entity_id=trade_id, trade_pk=trade.id, trade_id=trade_id, queue_id=trade.queue_id,
                      trade_date=trade.trade_date, settlement_date=trade.settlement_date,
                      risk_score=trade.final_score, status_after=trade.status, likely_reason=trade.likely_reason,
                      recommended_fix=trade.suggested_action,
                      details={"fileName": file_name, "events": len(events), "emailTo": recipients,
                               "emailStatus": email_status})
    db.commit()
    logger.info("Audit report %s generated by %s (%d events, e-mail %s)", file_name, user.username, len(events), email_status)
    return ReportOut(report_url=f"/api/v1/audit/reports/{file_name}", email_status=email_status, file_name=file_name,
                     email_to=recipients)


def find_report(db: Session, file_name: str, user: User) -> Path:
    """Only files this application generated, and only for trades the user may see."""
    if not FILE_NAME.match(file_name):
        raise NotFoundError("Report not found")
    stmt = select(AuditLog.id).where(AuditLog.action == "AUDIT_REPORT_GENERATED",
                                     AuditLog.details["fileName"].astext == file_name)
    known = db.scalar(audit_query_service._scope(stmt, user).limit(1))
    path = upload_service.REPORT_DIR / file_name
    if known is None or not path.is_file():
        raise NotFoundError("Report not found")
    return path
