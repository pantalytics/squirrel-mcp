"""SMTP send path for Soverin, using the standard library.

Nothing exotic: implicit TLS on 465 (SMTP_SSL) or STARTTLS on 587. No third-party
dependency -- this is the "don't reinvent the wheel, but SMTP is already trivial"
part.
"""

from __future__ import annotations

import datetime
import smtplib
import ssl
from email.message import Message
from email.utils import formatdate, getaddresses, make_msgid
from typing import List, Optional, Sequence

from ...logging_config import get_logger
from ..protocol import MailAuthError, MailProviderError, OutgoingAttachment
from .mime import all_recipients, build_email

logger = get_logger(__name__)

# Per-connection-attempt timeout. The stdlib tries each resolved address in
# turn, so a host with two A records behind a silently-dropping firewall costs
# 2x this before the error surfaces -- keep it short enough that a blocked
# port reads as a quick, clear failure rather than a minute of dead air.
SMTP_TIMEOUT = 10

# The socket timeout smtplib sets at connect covers every later operation --
# including the single ``sendall`` that pushes the whole DATA payload. At 10
# seconds flat that silently demanded a ~20 Mbit/s uplink to send 25 MB, so an
# attachment on a domestic connection would have died as a timeout that reads
# like an unreachable server. Stretch the budget by the payload instead: this
# is a ceiling, not a wait, so being generous costs nothing when the link is
# fast and rescues the send when it is not.
MIN_UPLOAD_BYTES_PER_SEC = 50 * 1024


def _timeout_for(size_bytes: int) -> int:
    return SMTP_TIMEOUT + size_bytes // MIN_UPLOAD_BYTES_PER_SEC


def _header_recipients(msg: Message) -> List[str]:
    """Every address a composed message is addressed to, To + Cc + Bcc."""
    pairs = getaddresses(
        msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", [])
    )
    seen: List[str] = []
    for _, addr in pairs:
        if addr and addr not in seen:
            seen.append(addr)
    return seen


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
        attachments: Optional[List[OutgoingAttachment]] = None,
        body_html: Optional[str] = None,
    ) -> dict:
        """Send a message and return {'message_id', 'recipients', 'raw', 'sent_at'}.

        ``in_reply_to``/``references`` come from the message being replied to
        (the provider reads them over IMAP) and are what put the reply in the
        thread rather than beside it.

        ``raw`` and ``sent_at`` exist for one caller: the provider files a copy
        of what went out in the account's Sent folder, and it can only do that
        with *these* bytes -- rebuilding the message would mint a second
        Message-ID and a second Date, so the copy would no longer be the mail
        the recipient got. The provider consumes both keys and does not pass
        them up to the tool layer.
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
            attachments=attachments,
            body_html=body_html,
        )
        return self._deliver_prepared(msg, all_recipients(to, cc, bcc))

    def send_existing(self, msg: Message) -> dict:
        """Deliver a message somebody already composed -- a draft -- as it stands.

        The counterpart of ``send``, and the reason both end in
        ``_deliver_prepared``: a draft is already a complete RFC 5322 message,
        so rebuilding it from fields the tool layer re-read would be a second,
        subtly different message -- a different Message-ID, a different MIME
        tree, and whatever the composer put in that the tools do not model
        quietly gone. What leaves is what was reviewed.

        Two headers are the exception, and both would be wrong left alone:

        * ``Date`` is re-stamped to now. A draft's Date is when it was
          *written*, and a message that went out this afternoon carrying this
          morning's timestamp sorts into the recipient's inbox at the wrong
          place -- above mail they have already read.
        * ``Message-ID`` is minted when the draft has none (ours always do;
          another client's need not), because it is what the Sent copy is
          matched on and what a reply to this message threads onto.

        Recipients come from the message's own headers -- the draft is the
        only statement of who it is for.
        """
        recipients = _header_recipients(msg)
        if not recipients:
            raise MailProviderError(
                "This draft has no recipients: fill in 'to' with mail_edit_draft first."
            )
        del msg["Date"]
        msg["Date"] = formatdate(localtime=True)
        if not msg["Message-ID"]:
            msg["Message-ID"] = make_msgid()
        return self._deliver_prepared(msg, recipients)

    def _deliver_prepared(self, msg: Message, recipients: Sequence[str]) -> dict:
        """Put a finished message on the wire. The one delivery path."""
        recipients = list(recipients)
        if not recipients:
            raise MailProviderError("No recipients: 'to' is required")

        message_id = msg["Message-ID"]
        # The Sent copy is taken *before* the Bcc header comes off: a copy in
        # your own Sent folder is the only record you keep of whom you bcc'd,
        # and the draft path stores it for the same reason. Only what leaves
        # the machine has to hide it -- BCC must not travel in the headers.
        raw = msg.as_bytes()
        delivered_size = len(raw)
        if "Bcc" in msg:
            del msg["Bcc"]
            delivered_size = len(msg.as_bytes())

        try:
            self._deliver(msg, recipients, delivered_size)
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
        return {
            "message_id": message_id,
            "recipients": recipients,
            "raw": raw,
            "sent_at": datetime.datetime.now(datetime.timezone.utc),
        }

    def _deliver(self, msg: Message, recipients: List[str], size_bytes: int) -> None:
        timeout = _timeout_for(size_bytes)
        if self._security == "ssl":
            with smtplib.SMTP_SSL(
                self._host, self._port, timeout=timeout, context=self._ssl_context()
            ) as server:
                server.login(self._username, self._password)
                self._check_size(server, size_bytes)
                server.send_message(msg, from_addr=self._email, to_addrs=recipients)
            return

        with smtplib.SMTP(self._host, self._port, timeout=timeout) as server:
            server.ehlo()
            if self._security == "starttls":
                server.starttls(context=self._ssl_context())
                server.ehlo()
            server.login(self._username, self._password)
            self._check_size(server, size_bytes)
            server.send_message(msg, from_addr=self._email, to_addrs=recipients)

    @staticmethod
    def _check_size(server: smtplib.SMTP, size_bytes: int) -> None:
        """Refuse before DATA what the server would refuse after it.

        SMTP's SIZE extension (RFC 1870) has the server advertise its own
        maximum in the EHLO reply, so there is nothing to look up per provider
        and nothing to keep up to date in a table -- Soverin answers
        ``SIZE 73400320`` (70 MiB), Gmail 35 MiB, and each says so itself.
        Asking it here turns "552 message too large" arriving after a full
        upload into an error that names both numbers before a byte is sent.

        A server that advertises no SIZE, or advertises 0 (meaning "no stated
        limit"), is left alone -- guessing a ceiling for it would invent the
        very failure this avoids.
        """
        try:
            limit = int(server.esmtp_features.get("size", 0))
        except (TypeError, ValueError):
            return
        if limit and size_bytes > limit:
            raise MailProviderError(
                f"Message is {size_bytes / 1_048_576:.1f} MB, over this server's "
                f"{limit / 1_048_576:.1f} MB limit. Attachments travel base64-encoded, "
                f"which adds about a third -- send a smaller file or a link instead."
            )
