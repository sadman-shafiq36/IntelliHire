# builds the invitation email and sends it over smtp when google isn't connected. gmail
# sends the same bytes
#
# with a calendar invitation the message is laid out the way calendar apps send theirs,
# so gmail and outlook show it as an invite with "add to calendar":
#
#   multipart/mixed
#   ├── multipart/alternative
#   │   ├── text/plain
#   │   ├── text/html
#   │   └── text/calendar; method=REQUEST (or CANCEL)
#   └── invite.ics (application/ics), for apps that only read attachments

import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from settings import settings


def build_message(
    to: str,
    subject: str,
    body: str,
    html: str | None = None,
    ics: str | None = None,
    ics_method: str = "REQUEST",
    from_address: str | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    if from_address:
        msg["From"] = from_address
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="intellihire.local")
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    if ics:
        msg.add_alternative(ics, subtype="calendar", params={"method": ics_method})
        msg.add_attachment(
            ics.encode(), maintype="application", subtype="ics", filename="invite.ics"
        )
    return msg


def send_smtp(msg: EmailMessage) -> str:
    if "From" not in msg:
        msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_from_email))
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
    return msg["Message-ID"]
