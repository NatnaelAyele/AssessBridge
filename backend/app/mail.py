import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from urllib.parse import urlencode, urljoin

from .config import SEB_BASE_URL, SEB_CONFIG_PASSWORD, SEB_REQUIRED


class MailNotConfigured(Exception):
    pass


EXAM_ENTRY_URL = os.getenv("EXAM_ENTRY_URL", "http://127.0.0.1:8000/exam-entry").strip()
REGISTER_URL = urljoin(EXAM_ENTRY_URL, "/register")


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    security: str
    username: str
    password: str
    sender: str

    @classmethod
    def from_environment(cls):
        host = os.getenv("SMTP_HOST", "").strip()
        username = os.getenv("SMTP_USERNAME", "").strip()
        password = os.getenv("SMTP_PASSWORD", "")
        sender = os.getenv("SMTP_SENDER", "").strip()
        security = os.getenv("SMTP_SECURITY", "starttls").lower()
        try:
            port = int(os.getenv("SMTP_PORT", "587"))
        except ValueError as exc:
            raise MailNotConfigured("Invalid SMTP port") from exc
        if not all((host, username, password, sender)) or security not in {"starttls", "ssl"} or not 1 <= port <= 65535:
            raise MailNotConfigured("SMTP settings are incomplete")
        return cls(host, port, security, username, password, sender)


def calendar_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def fold_line(line: str) -> list[str]:
    lines = []
    current = ""
    length = 0
    for character in line:
        size = len(character.encode("utf-8"))
        if length + size > 75:
            lines.append(current)
            current = " " + character
            length = size + 1
        else:
            current += character
            length += size
    lines.append(current)
    return lines


def calendar_event(*, assessment_id: int, invitation_id: int, title: str, start: datetime, end: datetime, sender: str, recipient: str, exam_link: str | None = None) -> str:
    def stamp(moment: datetime) -> str:
        return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    domain = sender.rsplit("@", 1)[-1]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AssessBridge//Recruitment Assessment//EN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:assessment-{assessment_id}-invite-{invitation_id}@{domain}",
        f"DTSTAMP:{stamp(datetime.now(timezone.utc))}",
        f"DTSTART:{stamp(start)}",
        f"DTEND:{stamp(end)}",
        f"SUMMARY:{calendar_escape(title)}",
        f"DESCRIPTION:{calendar_escape('AssessBridge recruitment assessment access window')}",
        *([f"URL:{exam_link}"] if exam_link else []),
        f"ORGANIZER:mailto:{sender}",
        f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=FALSE:mailto:{recipient}",
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(folded for line in lines for folded in fold_line(line)) + "\r\n"


def send_calendar_invitation(
    *, settings: MailSettings, assessment_id: int, invitation_id: int, title: str,
    duration_minutes: int, start: datetime, end: datetime, candidate_name: str,
    candidate_email: str, username: str, password: str,
    seb_launch_url: str | None = None, seb_quit_password: str | None = None,
    seb_config_password: str | None = None,
    registered: bool | None = None,
) -> None:
    configuration_password = seb_config_password if seb_config_password is not None else SEB_CONFIG_PASSWORD
    east_africa_time = timezone(timedelta(hours=3))
    local_start = start.astimezone(east_africa_time)
    local_end = end.astimezone(east_africa_time)
    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = candidate_email
    message["Subject"] = f"AssessBridge invitation: {title}"
    registration_link = REGISTER_URL + "?" + urlencode({"email": candidate_email})
    account_instruction = (
        f"Your existing candidate account uses {candidate_email}. Sign in with that account.\n"
        if registered else
        f"Before opening SEB, use a regular browser to create and verify an account with this exact email address: {registration_link}\n"
    )
    config_link = seb_launch_url or f"{SEB_BASE_URL}/seb/config"
    launch_page = urljoin(config_link, "/seb/open/" + username)
    seb_instruction = (
        f"Open this exam link in your regular browser, then select Open in SEB: {launch_page}\n"
        f"If SEB does not open, download the configuration at {config_link} and open it with SEB.\n"
        f"Configuration password: {configuration_password}\n"
        + (f"Emergency SEB quit code: {seb_quit_password}\n" if seb_quit_password else "")
        + "You can also use Exit SEB on the exam entry page without entering a code.\n"
        + "If you leave after starting the exam, that attempt is disqualified and cannot be resumed.\n"
        + "SEB opens the exam entry page. Sign in and enter the exam-only credentials above.\n"
    )
    message.set_content(
        f"Hello {candidate_name},\n\n"
        f"You are invited to take {title}.\n"
        f"Assessment access window: {local_start.strftime('%Y-%m-%d %H:%M')} to {local_end.strftime('%Y-%m-%d %H:%M')} (East Africa Time, UTC+03:00).\n"
        f"Exam duration: {duration_minutes} minutes.\n"
        f"Exam-only username: {username}\n"
        f"Exam-only password: {password}\n"
        + "\n" + account_instruction
        + (f"Install Safe Exam Browser from https://safeexambrowser.org/download/.\n{seb_instruction}" if SEB_REQUIRED else f"Open {EXAM_ENTRY_URL}, sign in, and use these exam-only credentials.\n")
        + "These credentials are for this assessment only. Keep them private.\n"
    )
    if SEB_REQUIRED:
        message.add_alternative(
            "<html><body>"
            f"<p>Hello {escape(candidate_name)},</p>"
            f"<p>You are invited to take <strong>{escape(title)}</strong>.</p>"
            f"<p>Access window: {escape(local_start.strftime('%Y-%m-%d %H:%M'))} to {escape(local_end.strftime('%Y-%m-%d %H:%M'))} EAT. Duration: {duration_minutes} minutes.</p>"
            f"<p>Exam-only username: <strong>{escape(username)}</strong><br>Exam-only password: <strong>{escape(password)}</strong></p>"
            + (f'<p>First, <a href="{escape(registration_link, quote=True)}">register and verify this email in a regular browser</a>. Finish verification before opening SEB.</p>' if not registered else f"<p>Sign in with your existing candidate account: {escape(candidate_email)}</p>")
            + f'<p><a href="{escape(launch_page, quote=True)}">Open your exam launch page</a> in a regular browser, then choose Open in SEB.</p>'
            + f'<p>If needed, <a href="{escape(config_link, quote=True)}">download the SEB configuration</a>. Configuration password: <strong>{escape(configuration_password)}</strong>.</p>'
            + (f"<p>Emergency SEB quit code: <strong>{escape(seb_quit_password)}</strong>. You can also use Exit SEB on the exam entry page.</p>" if seb_quit_password else "")
            + "<p>Leaving after starting the exam disqualifies the attempt. Keep these credentials private.</p>"
            "</body></html>", subtype="html",
        )
    message.add_alternative(
        calendar_event(
            assessment_id=assessment_id, invitation_id=invitation_id, title=title,
            start=start, end=end, sender=settings.sender, recipient=candidate_email, exam_link=launch_page if SEB_REQUIRED else EXAM_ENTRY_URL,
        ),
        subtype="calendar",
        charset="utf-8",
        params={"method": "REQUEST"},
    )

    _send_message(settings, message)


def send_verification_email(*, settings: MailSettings, email: str, username: str, token: str) -> None:
    link = f"{urljoin(EXAM_ENTRY_URL, '/verify-email')}#token={token}"
    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = email
    message["Subject"] = "Verify your AssessBridge candidate email"
    message.set_content(
        "Hello,\n\n"
        f"Open this link to verify your candidate account: {link}\n\n"
        "The link expires in one hour. If you did not register, you can ignore this message.\n"
    )
    _send_message(settings, message)


def _send_message(settings: MailSettings, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    if settings.security == "ssl":
        with smtplib.SMTP_SSL(settings.host, settings.port, timeout=15, context=context) as smtp:
            smtp.login(settings.username, settings.password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(settings.host, settings.port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(settings.username, settings.password)
            smtp.send_message(message)
