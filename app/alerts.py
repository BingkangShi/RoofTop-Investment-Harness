"""SMTP alert outbox with safe dry-run defaults and deduplication."""

import os
import smtplib
from contextlib import closing
from datetime import datetime, timezone
from email.message import EmailMessage

from .db import connect


def queue_alert(dedupe_key: str, subject: str, body: str) -> bool:
    with closing(connect()) as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO alert_outbox(dedupe_key,channel,subject,body,status,created_at)
               VALUES(?,'EMAIL',?,?,'PENDING',?)""",
            (dedupe_key, subject, body, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cursor.rowcount == 1


def smtp_status() -> dict:
    required = ("ROOFTOP_SMTP_HOST", "ROOFTOP_SMTP_USER", "ROOFTOP_SMTP_PASSWORD", "ROOFTOP_ALERT_TO")
    return {"configured": all(os.environ.get(key) for key in required),
            "send_enabled": os.environ.get("ROOFTOP_EMAIL_SEND_ENABLED") == "1",
            "default": "dry_run"}


def send_pending() -> dict:
    status = smtp_status()
    if not status["configured"] or not status["send_enabled"]:
        return {"status": "DRY_RUN", "sent": 0, **status}
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM alert_outbox WHERE status='PENDING' ORDER BY id LIMIT 20").fetchall()
        sent = 0
        try:
            with smtplib.SMTP_SSL(os.environ["ROOFTOP_SMTP_HOST"], int(os.environ.get("ROOFTOP_SMTP_PORT", "465"))) as client:
                client.login(os.environ["ROOFTOP_SMTP_USER"], os.environ["ROOFTOP_SMTP_PASSWORD"])
                for row in rows:
                    message = EmailMessage()
                    message["From"] = os.environ["ROOFTOP_SMTP_USER"]
                    message["To"] = os.environ["ROOFTOP_ALERT_TO"]
                    message["Subject"] = row["subject"]
                    message.set_content(row["body"])
                    client.send_message(message)
                    conn.execute("UPDATE alert_outbox SET status='SENT',sent_at=? WHERE id=?",
                                 (datetime.now(timezone.utc).isoformat(), row["id"]))
                    sent += 1
            conn.commit()
            return {"status": "SENT", "sent": sent}
        except Exception as exc:
            conn.execute("UPDATE alert_outbox SET status='FAILED',error=? WHERE status='PENDING'", (repr(exc),))
            conn.commit()
            return {"status": "FAILED", "sent": sent, "error": repr(exc)}
