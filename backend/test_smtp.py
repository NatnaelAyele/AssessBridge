"""Send one harmless test email to an address entered at the prompt."""

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

from app.mail import MailNotConfigured, MailSettings


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    try:
        settings = MailSettings.from_environment()
    except MailNotConfigured as exc:
        raise SystemExit(f"SMTP is not configured: {exc}") from exc

    recipient = input("Send a test email to: ").strip()
    if not recipient or "@" not in recipient or "\n" in recipient or "\r" in recipient:
        raise SystemExit("Enter one valid recipient email address.")

    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = recipient
    message["Subject"] = "AssessBridge email setup test"
    message.set_content("AssessBridge can send email from this account. No candidate invitation was sent.")

    context = ssl.create_default_context()
    try:
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
    except (OSError, smtplib.SMTPException) as exc:
        raise SystemExit(f"SMTP test failed: {exc}") from exc
    print("SMTP test accepted by the mail server. Check the recipient inbox and spam folder.")


if __name__ == "__main__":
    main()
