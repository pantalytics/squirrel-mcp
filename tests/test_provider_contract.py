"""Guards the provider seam itself, the way pan_mail_pro guards its
``mail.provider.client``: the registry and the config agree on which backends
exist, every registered backend implements the whole ``MailProvider`` protocol,
and the tool layer never touches a concrete client.

The protocol is structural (``typing.Protocol``), so nothing fails at import
time when a backend misses a method -- it fails at the first call, at runtime,
in a user's session. These tests move that failure to ``make test``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from squirrel_mcp.config import SUPPORTED_MAIL_PROVIDERS, SquirrelConfig
from squirrel_mcp.providers.factory import (
    MAIL_PROVIDER_REGISTRY,
    create_mail_provider,
)
from squirrel_mcp.providers.protocol import MailProvider

TOOLS_DIR = Path(__file__).resolve().parent.parent / "squirrel_mcp" / "tools"

# The tool layer may only see providers.protocol. Any of these names appearing
# under tools/ means a concrete transport leaked past the seam.
FORBIDDEN_IN_TOOLS = ("soverin", "imap_tools", "imaplib", "smtplib", "caldav")


def _protocol_members(protocol: type) -> list:
    return [name for name in dir(protocol) if not name.startswith("_")]


class TestProviderRegistry:
    def test_registry_and_config_name_the_same_backends(self):
        """`config` validates the name, `factory` builds from it. Two lists,
        one meaning -- so they must be mechanically held equal."""
        assert set(MAIL_PROVIDER_REGISTRY) == set(SUPPORTED_MAIL_PROVIDERS)

    def test_every_registered_backend_implements_the_whole_protocol(self):
        members = _protocol_members(MailProvider)
        assert members, "MailProvider protocol reports no public members"
        for name, loader in MAIL_PROVIDER_REGISTRY.items():
            cls = loader()
            missing = [m for m in members if not hasattr(cls, m)]
            assert not missing, (
                f"Backend {name!r} ({cls.__name__}) misses protocol "
                f"members: {missing}"
            )

    def test_unknown_provider_fails_naming_the_supported_set(self):
        config = SquirrelConfig(mail_provider="carrier-pigeon", skip_validation=True)
        with pytest.raises(ValueError, match="carrier-pigeon"):
            create_mail_provider(config)


class TestToolLayerBoundary:
    """Tools never import a concrete client -- previously a convention in
    CLAUDE.md, now a test, for the reason pan_mail_pro greps its boundary in
    CI: a convention nobody can check is a convention that is already broken
    somewhere."""

    def test_no_concrete_backend_reference_under_tools(self):
        pattern = re.compile("|".join(FORBIDDEN_IN_TOOLS))
        offenders = []
        for path in sorted(TOOLS_DIR.rglob("*.py")):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if pattern.search(line):
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        assert not offenders, (
            "The tool layer references a concrete backend; it may only "
            "import providers.protocol:\n" + "\n".join(offenders)
        )

    def test_tools_dir_exists_and_is_scanned(self):
        assert TOOLS_DIR.is_dir()
        assert list(TOOLS_DIR.rglob("*.py")), "boundary scan found no files"
