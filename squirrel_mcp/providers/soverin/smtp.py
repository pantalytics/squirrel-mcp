"""SMTP send path for Soverin, using the standard library.

Nothing exotic: implicit TLS on 465 (SMTP_SSL) or STARTTLS on 587. No third-party
dependency -- this is the "don't reinvent the wheel, but SMTP is already trivial"
part.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import List, Optional

from ...logging_config import get_logger
from ..protocol import MailAuthError, MailProviderError
from .mime import all_recipients, build_email

logger = get_logger(__name__)


class SoverinSmtpClient:
    """Thin SMTP sender. One connection per send (simple and robust)."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        email: str,
        security: str = "ssl",
        tls_verify: bool = True,
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._email = email
        self._security = security
        self._tls_verify = tls_verify

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not self._tls_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def send(
        self,
        to: List[str],
        subject: str,
        body: str,
        *,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
    ) -> dict:
        """Send a message and return {'message_id', 'recipients'}."""
        msg = build_email(self._email, to, subject, body, cc=cc, bcc=bcc)
        recipients = all_recipients(to, cc, bcc)
        if not recipients:
            raise MailProviderError("No recipients: 'to' is required")

        # BCC must not travel in the message headers.
        message_id = msg["Message-ID"]
        if "Bcc" in msg:
            del msg["Bcc"]

        try:
            self._deliver(msg, recipients)
        except smtplib.SMTPAuthenticationError as exc:
            raise MailAuthError(f"SMTP authentication failed: {exc}") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise MailProviderError(f"Sending failed: {exc}") from exc

        logger.info("Sent message %s to %d recipient(s)", message_id, len(recipients))
        return {"message_id": message_id, "recipients": recipients}

    def _deliver(self, msg: EmailMessage, recipients: List[str]) -> None:
        if self._security == "ssl":
            with smtplib.SMTP_SSL(
                self._host, self._port, timeout=30, context=self._ssl_context()
            ) as server:
                server.login(self._username, self._password)
                server.send_message(msg, from_addr=self._email, to_addrs=recipients)
            return

        with smtplib.SMTP(self._host, self._port, timeout=30) as server:
            server.ehlo()
            if self._security == "starttls":
                server.starttls(context=self._ssl_context())
                server.ehlo()
            server.login(self._username, self._password)
            server.send_message(msg, from_addr=self._email, to_addrs=recipients)
