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

# Per-connection-attempt timeout. The stdlib tries each resolved address in
# turn, so a host with two A records behind a silently-dropping firewall costs
# 2x this before the error surfaces -- keep it short enough that a blocked
# port reads as a quick, clear failure rather than a minute of dead air.
SMTP_TIMEOUT = 10


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
        in_reply_to: Optional[str] = None,
        references: Optional[List[str]] = None,
    ) -> dict:
        """Send a message and return {'message_id', 'recipients'}.

        ``in_reply_to``/``references`` come from the message being replied to
        (the provider reads them over IMAP) and are what put the reply in the
        thread rather than beside it.
        """
        msg = build_email(
            self._email,
            to,
            subject,
            body,
            cc=cc,
            bcc=bcc,
            in_reply_to=in_reply_to,
            references=references,
        )
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
        except smtplib.SMTPException as exc:
            raise MailProviderError(f"Sending failed: {exc}") from exc
        except OSError as exc:
            # A socket-level failure is a reachability problem, not a message
            # problem. Name the endpoint: "Network is unreachable" against a
            # host whose IMAP works fine means the SMTP *port* is blocked
            # somewhere on the path (hosting providers commonly block outbound
            # 25/465), and the settings the user would otherwise re-check are
            # not the fault.
            raise MailProviderError(
                f"Could not reach SMTP server {self._host}:{self._port} ({exc}). "
                f"The host may be down or this port blocked along the way -- if "
                f"reading mail works, the credentials and host are fine; try the "
                f"provider's STARTTLS port (587) or check outbound-SMTP blocking."
            ) from exc

        logger.info("Sent message %s to %d recipient(s)", message_id, len(recipients))
        return {"message_id": message_id, "recipients": recipients}

    def _deliver(self, msg: EmailMessage, recipients: List[str]) -> None:
        if self._security == "ssl":
            with smtplib.SMTP_SSL(
                self._host, self._port, timeout=SMTP_TIMEOUT, context=self._ssl_context()
            ) as server:
                server.login(self._username, self._password)
                server.send_message(msg, from_addr=self._email, to_addrs=recipients)
            return

        with smtplib.SMTP(self._host, self._port, timeout=SMTP_TIMEOUT) as server:
            server.ehlo()
            if self._security == "starttls":
                server.starttls(context=self._ssl_context())
                server.ehlo()
            server.login(self._username, self._password)
            server.send_message(msg, from_addr=self._email, to_addrs=recipients)
