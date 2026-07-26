"""The SMTP send path's failure story: a socket-level failure must name the
endpoint it could not reach, because "Network is unreachable" against a host
whose IMAP works fine sends the user off to re-check settings that are right.
"""

import smtplib

import pytest

from squirrel_mcp.providers.protocol import MailAuthError, MailProviderError
from squirrel_mcp.providers.soverin import smtp as smtp_mod
from squirrel_mcp.providers.soverin.smtp import SoverinSmtpClient


@pytest.fixture
def client():
    return SoverinSmtpClient(
        host="smtp.example.net",
        port=465,
        username="me@example.net",
        password="pw",
        email="me@example.net",
    )


def test_socket_failure_names_host_and_port(client, monkeypatch):
    def boom(msg, recipients):
        raise OSError(101, "Network is unreachable")

    monkeypatch.setattr(client, "_deliver", boom)
    with pytest.raises(MailProviderError) as e:
        client.send(["a@b.com"], "s", "b")
    text = str(e.value)
    assert "smtp.example.net:465" in text
    assert "blocked" in text  # points at the real suspect, not the settings


def test_smtp_protocol_failure_keeps_the_plain_message(client, monkeypatch):
    def boom(msg, recipients):
        raise smtplib.SMTPRecipientsRefused({"a@b.com": (550, b"no")})

    monkeypatch.setattr(client, "_deliver", boom)
    with pytest.raises(MailProviderError) as e:
        client.send(["a@b.com"], "s", "b")
    assert "Sending failed" in str(e.value)


def test_auth_failure_is_an_auth_error(client, monkeypatch):
    def boom(msg, recipients):
        raise smtplib.SMTPAuthenticationError(535, b"bad creds")

    monkeypatch.setattr(client, "_deliver", boom)
    with pytest.raises(MailAuthError):
        client.send(["a@b.com"], "s", "b")


def test_timeout_is_short_enough_to_fail_fast():
    """Two stale A records must fail in well under a minute, not 60+ seconds."""
    assert smtp_mod.SMTP_TIMEOUT <= 15
