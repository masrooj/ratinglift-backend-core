"""Message senders for OTPs and transactional emails.

Defaults to logging senders (dev). If SMTP settings are configured, an
``SmtpEmailSender`` is used for emails. SMS remains a logging stub until a
provider (Twilio/SNS) is wired up.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class OtpSender(Protocol):
    def send(self, destination: str, otp: str, purpose: str) -> None: ...


class EmailSender(Protocol):
    def send(self, destination: str, otp: str, purpose: str) -> None: ...
    def send_message(self, destination: str, subject: str, body: str) -> None: ...


class LoggingEmailSender:
    channel = "email"

    def send(self, destination: str, otp: str, purpose: str) -> None:
        logger.info("mfa_email_otp destination=%s purpose=%s otp=%s", destination, purpose, otp)

    def send_message(self, destination: str, subject: str, body: str) -> None:
        logger.info("email_message destination=%s subject=%s body=%s", destination, subject, body)


class LoggingSmsSender:
    channel = "sms"

    def send(self, destination: str, otp: str, purpose: str) -> None:
        logger.info("mfa_sms_otp destination=%s purpose=%s otp=%s", destination, purpose, otp)


class TwilioSmsSender:
    """Sends SMS via Twilio. Falls back to logging on any error."""

    channel = "sms"

    def __init__(self) -> None:
        self.account_sid = settings.twilio_account_sid
        self.auth_token = settings.twilio_auth_token
        self.from_number = settings.twilio_from_number
        self._client = None
        try:
            from twilio.rest import Client  # type: ignore

            self._client = Client(self.account_sid, self.auth_token)
        except Exception as exc:  # noqa: BLE001
            logger.error("twilio_init_failed error=%s", exc)

    def send(self, destination: str, otp: str, purpose: str) -> None:
        body = f"Your RatingLift verification code is: {otp}"
        if not self._client or not self.from_number:
            logger.warning(
                "twilio_not_configured fallback_logging destination=%s purpose=%s otp=%s",
                destination,
                purpose,
                otp,
            )
            return
        try:
            self._client.messages.create(to=destination, from_=self.from_number, body=body)
            logger.info("twilio_sms_sent destination=%s purpose=%s", destination, purpose)
        except Exception as exc:  # noqa: BLE001
            logger.error("twilio_send_failed destination=%s error=%s", destination, exc)


class SmtpEmailSender:
    """Sends email via SMTP. Falls back to logging if SMTP is misconfigured."""

    channel = "email"

    def __init__(self) -> None:
        self.host = settings.smtp_host
        self.port = settings.smtp_port
        self.user = settings.smtp_user
        self.password = settings.smtp_password
        self.sender = settings.smtp_from or settings.smtp_user or "no-reply@ratinglift.local"
        self.use_tls = settings.smtp_use_tls

    def _send(self, destination: str, subject: str, body: str) -> None:
        if not self.host:
            logger.warning(
                "smtp_not_configured fallback_logging destination=%s subject=%s",
                destination,
                subject,
            )
            logger.info("email_body=%s", body)
            return
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = destination
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
                if self.use_tls:
                    smtp.starttls()
                if self.user and self.password:
                    smtp.login(self.user, self.password)
                smtp.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            logger.error("smtp_send_failed destination=%s error=%s", destination, exc)

    def send(self, destination: str, otp: str, purpose: str) -> None:
        subject = "Your verification code"
        body = (
            f"Your RatingLift verification code is: {otp}\n\n"
            f"Purpose: {purpose}\n"
            "The code expires shortly. Do not share it with anyone."
        )
        self._send(destination, subject, body)

    def send_message(self, destination: str, subject: str, body: str) -> None:
        self._send(destination, subject, body)


def _default_email_sender() -> EmailSender:
    if settings.smtp_host:
        return SmtpEmailSender()
    return LoggingEmailSender()


def _default_sms_sender() -> OtpSender:
    if settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number:
        return TwilioSmsSender()
    return LoggingSmsSender()


_email_sender: EmailSender = _default_email_sender()
_sms_sender: OtpSender = _default_sms_sender()


def get_email_sender() -> EmailSender:
    return _email_sender


def get_sms_sender() -> OtpSender:
    return _sms_sender


def set_email_sender(sender: EmailSender) -> None:
    """Override the email sender (used by tests and prod wiring)."""
    global _email_sender
    _email_sender = sender


def set_sms_sender(sender: OtpSender) -> None:
    global _sms_sender
    _sms_sender = sender
