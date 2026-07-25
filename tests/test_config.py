"""Config validation. No provider presets -- every host is explicit."""

import pytest

from squirrel_mcp.config import SquirrelConfig


def _cfg(**overrides) -> SquirrelConfig:
    """A valid config; override one field per test to exercise validation."""
    return SquirrelConfig(
        **{
            "mail_email": "me@x.eu",
            "mail_password": "pw",
            "imap_host": "imap.x.eu",
            "smtp_host": "smtp.x.eu",
            **overrides,
        }
    )


def test_port_defaults_are_implicit_tls():
    cfg = _cfg()
    assert cfg.imap_port == 993
    assert cfg.smtp_port == 465


def test_provider_defaults_to_imap():
    assert _cfg().mail_provider == "imap"


def test_missing_email_raises():
    with pytest.raises(ValueError, match="SQUIRREL_MAIL_EMAIL"):
        _cfg(mail_email="")


def test_missing_password_raises():
    with pytest.raises(ValueError, match="SQUIRREL_MAIL_PASSWORD"):
        _cfg(mail_password="")


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown SQUIRREL_MAIL_PROVIDER"):
        _cfg(mail_provider="carrierpigeon")


def test_missing_imap_host_raises():
    with pytest.raises(ValueError, match="SQUIRREL_IMAP_HOST"):
        _cfg(imap_host="")


def test_missing_smtp_host_raises():
    with pytest.raises(ValueError, match="SQUIRREL_SMTP_HOST"):
        _cfg(smtp_host="")


def test_limit_ordering_validated():
    with pytest.raises(ValueError, match="cannot exceed"):
        _cfg(default_limit=200, max_limit=100)


def test_bad_port_raises():
    with pytest.raises(ValueError, match="between 1 and 65535"):
        _cfg(imap_port=99999)


def test_skip_validation_allows_empty():
    cfg = SquirrelConfig(skip_validation=True)
    assert cfg.mail_email == ""


def test_login_username_defaults_to_email():
    assert _cfg().login_username == "me@x.eu"


def test_login_username_override():
    assert _cfg(mail_username="me").login_username == "me"


def test_security_defaults_to_ssl():
    cfg = _cfg()
    assert cfg.imap_security == "ssl" and cfg.smtp_security == "ssl" and cfg.tls_verify is True


def test_invalid_security_raises():
    with pytest.raises(ValueError, match="SQUIRREL_IMAP_SECURITY"):
        _cfg(imap_security="carrier")


def test_dav_pillars_off_unless_configured():
    cfg = _cfg()
    assert cfg.caldav_url == "" and cfg.carddav_url == ""
    assert not cfg.calendar_enabled and not cfg.contacts_enabled


def test_dav_urls_enable_their_pillar():
    cfg = _cfg(caldav_url="https://dav.x.eu", carddav_url="https://dav.x.eu")
    assert cfg.calendar_enabled and cfg.contacts_enabled
