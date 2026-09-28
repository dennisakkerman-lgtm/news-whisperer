"""Monday e-mail with the HTML digest. Uses Resend (RESEND_API_KEY) or SMTP (SMTP_HOST...)."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import requests

log = logging.getLogger(__name__)


def send_report(cfg, run) -> bool:
    to = [a.strip() for a in os.environ.get("REPORT_TO", "").split(",") if a.strip()]
    sender = os.environ.get("REPORT_FROM", "News Whisperer <whisperer@seeders.com>")
    if not to:
        log.info("REPORT_TO not set; skipping e-mail")
        return False
    subject = f"{cfg['brand']['name']} News Whisperer · week of {run.started_at:%d %b %Y}"
    panel = os.environ.get("PUBLIC_URL", "").rstrip("/")
    html = run.report_html
    if panel:
        html = html.replace("<!--PANEL_LINK-->",
                            f'<p><a href="{panel}/runs/{run.id}">Open in the panel</a></p>')

    if os.environ.get("RESEND_API_KEY"):
        r = requests.post("https://api.resend.com/emails", timeout=30,
                          headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
                          json={"from": sender, "to": to, "subject": subject, "html": html})
        r.raise_for_status()
        return True

    if os.environ.get("SMTP_HOST"):
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(to)
        msg.set_content("HTML e-mail; open in a client that shows HTML.")
        msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587))) as smtp:
            smtp.starttls()
            if os.environ.get("SMTP_USER"):
                smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(msg)
        return True

    log.warning("No RESEND_API_KEY or SMTP_HOST; report stored but not e-mailed")
    return False
