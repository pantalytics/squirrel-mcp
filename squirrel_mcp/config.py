"""Configuration management for the Squirrel MCP server.

Loads and validates environment variables (optionally from a .env file) into a
single ``SquirrelConfig``. Secrets live only in the environment / .env, never in
code. No hardcoded fallbacks for credentials -- explicit config or a clear error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv

# Backends Squirrel knows how to build. "imap" is any IMAP/SMTP mailbox -- you
# supply the host names yourself; there are no per-provider presets. Later,
# API-based backends ("gmail", "outlook") get added here.
SUPPORTED_MAIL_PROVIDERS = ("imap",)


@dataclass
class SquirrelConfig:
    """Configuration for the mail provider connection and MCP server settings."""

    # Provider selection
    mail_provider: str = "imap"

    # Mailbox credentials (required unless skip_validation)
    mail_email: str = ""
    mail_password: str = ""
    # Login username, if it differs from the email address. Most providers log in
    # with the full address, so this defaults to it.
    mail_username: str = ""

    # IMAP (incoming). Host is required -- look it up with your mail provider.
    imap_host: str = ""
    imap_port: int = 993

    # SMTP (outgoing). Host is required -- look it up with your mail provider.
    smtp_host: str = ""
    smtp_port: int = 465

    # Transport security. Defaults are the common secure setup (implicit TLS).
    # "plain" is for a trusted local test server only (e.g. GreenMail in CI).
    imap_security: Literal["ssl", "starttls", "plain"] = "ssl"
    smtp_security: Literal["ssl", "starttls", "plain"] = "ssl"
    # Verify TLS certificates. Keep true against real providers; disable only for
    # a local test server with a self-signed cert.
    tls_verify: bool = True

    # DAV endpoints for the contacts (CardDAV) and calendar (CalDAV) pillars.
    # Blank disables that pillar. They reuse the same login (login_username) and
    # password as mail.
    caldav_url: str = ""
    carddav_url: str = ""

    # Behaviour
    log_level: str = "INFO"
    default_limit: int = 25
    max_limit: int = 100
    max_body_chars: int = 20000

    # MCP transport
    transport: Literal["stdio", "streamable-http"] = "stdio"
    host: str = "localhost"
    port: int = 8000

    # Skip validation for multi-tenant mode (no single mailbox at startup)
    skip_validation: bool = False

    def __post_init__(self):
        """Validate. Nothing is guessed -- every connection detail is explicit."""
        if self.skip_validation:
            return

        if self.mail_provider not in SUPPORTED_MAIL_PROVIDERS:
            raise ValueError(
                f"Unknown SQUIRREL_MAIL_PROVIDER: {self.mail_provider!r}. "
                f"Supported: {', '.join(sorted(SUPPORTED_MAIL_PROVIDERS))}"
            )

        if not self.mail_email:
            raise ValueError("SQUIRREL_MAIL_EMAIL is required")
        if not self.mail_password:
            raise ValueError("SQUIRREL_MAIL_PASSWORD is required")

        if not self.imap_host:
            raise ValueError("SQUIRREL_IMAP_HOST is required (ask your mail provider)")
        if not self.smtp_host:
            raise ValueError("SQUIRREL_SMTP_HOST is required (ask your mail provider)")

        for label, value in (
            ("SQUIRREL_IMAP_PORT", self.imap_port),
            ("SQUIRREL_SMTP_PORT", self.smtp_port),
            ("SQUIRREL_MCP_PORT", self.port),
        ):
            if value <= 0 or value > 65535:
                raise ValueError(f"{label} must be between 1 and 65535")

        if self.default_limit <= 0:
            raise ValueError("SQUIRREL_MCP_DEFAULT_LIMIT must be positive")
        if self.max_limit <= 0:
            raise ValueError("SQUIRREL_MCP_MAX_LIMIT must be positive")
        if self.default_limit > self.max_limit:
            raise ValueError("SQUIRREL_MCP_DEFAULT_LIMIT cannot exceed SQUIRREL_MCP_MAX_LIMIT")

        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_log_levels:
            raise ValueError(
                f"Invalid log level: {self.log_level}. "
                f"Must be one of: {', '.join(valid_log_levels)}"
            )

        valid_transports = {"stdio", "streamable-http"}
        if self.transport not in valid_transports:
            raise ValueError(
                f"Invalid transport: {self.transport}. "
                f"Must be one of: {', '.join(valid_transports)}"
            )

        valid_security = {"ssl", "starttls", "plain"}
        for label, value in (
            ("SQUIRREL_IMAP_SECURITY", self.imap_security),
            ("SQUIRREL_SMTP_SECURITY", self.smtp_security),
        ):
            if value not in valid_security:
                raise ValueError(f"{label} must be one of: {', '.join(sorted(valid_security))}")

    @property
    def login_username(self) -> str:
        """The username to authenticate with (falls back to the email address)."""
        return self.mail_username or self.mail_email

    @property
    def calendar_enabled(self) -> bool:
        return bool(self.caldav_url)

    @property
    def contacts_enabled(self) -> bool:
        return bool(self.carddav_url)

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None) -> "SquirrelConfig":
        """Create configuration from environment variables."""
        return load_config(env_file)


def _get_int_env(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{key} must be a valid integer") from None


def load_config(env_file: Optional[Path] = None) -> SquirrelConfig:
    """Load configuration from environment variables and an optional .env file."""
    if env_file:
        if not env_file.exists():
            raise ValueError(
                f"Configuration file not found: {env_file}\n"
                "Create a .env file based on .env.example."
            )
        load_dotenv(env_file)
    else:
        default_env = Path(".env")
        env_loaded = False
        if default_env.exists():
            load_dotenv(default_env)
            env_loaded = True
        if not env_loaded and not os.getenv("SQUIRREL_MAIL_EMAIL"):
            raise ValueError(
                "No .env file found and SQUIRREL_MAIL_EMAIL not set in environment.\n"
                "Create a .env file based on .env.example or set environment variables."
            )

    return SquirrelConfig(
        mail_provider=os.getenv("SQUIRREL_MAIL_PROVIDER", "imap").strip() or "imap",
        mail_email=os.getenv("SQUIRREL_MAIL_EMAIL", "").strip(),
        mail_password=os.getenv("SQUIRREL_MAIL_PASSWORD", ""),
        mail_username=os.getenv("SQUIRREL_MAIL_USERNAME", "").strip(),
        imap_host=os.getenv("SQUIRREL_IMAP_HOST", "").strip(),
        imap_port=_get_int_env("SQUIRREL_IMAP_PORT", 993),
        smtp_host=os.getenv("SQUIRREL_SMTP_HOST", "").strip(),
        smtp_port=_get_int_env("SQUIRREL_SMTP_PORT", 465),
        imap_security=os.getenv("SQUIRREL_IMAP_SECURITY", "ssl").strip(),  # type: ignore[arg-type]
        smtp_security=os.getenv("SQUIRREL_SMTP_SECURITY", "ssl").strip(),  # type: ignore[arg-type]
        tls_verify=os.getenv("SQUIRREL_TLS_VERIFY", "true").strip().lower() != "false",
        caldav_url=os.getenv("SQUIRREL_CALDAV_URL", "").strip(),
        carddav_url=os.getenv("SQUIRREL_CARDDAV_URL", "").strip(),
        log_level=os.getenv("SQUIRREL_MCP_LOG_LEVEL", "INFO").strip(),
        default_limit=_get_int_env("SQUIRREL_MCP_DEFAULT_LIMIT", 25),
        max_limit=_get_int_env("SQUIRREL_MCP_MAX_LIMIT", 100),
        max_body_chars=_get_int_env("SQUIRREL_MCP_MAX_BODY_CHARS", 20000),
        transport=os.getenv("SQUIRREL_MCP_TRANSPORT", "stdio").strip(),  # type: ignore[arg-type]
        host=os.getenv("SQUIRREL_MCP_HOST", "localhost").strip(),
        port=_get_int_env("SQUIRREL_MCP_PORT", 8000),
    )


# Singleton configuration instance
_config: Optional[SquirrelConfig] = None


def get_config() -> SquirrelConfig:
    """Get the singleton configuration instance, loading it on first use."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: SquirrelConfig) -> None:
    """Set the singleton configuration instance (primarily for testing)."""
    global _config
    _config = config


def reset_config() -> None:
    """Reset the singleton configuration instance (primarily for testing)."""
    global _config
    _config = None
