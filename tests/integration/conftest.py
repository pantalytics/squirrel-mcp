"""Integration-test fixtures: a real GreenMail IMAP/SMTP server in Docker.

If GreenMail is already listening on localhost:3143 (e.g. started via
docker-compose.test.yml), we reuse it. Otherwise we start it with docker compose
and tear it down at the end. If Docker is unavailable, the tests skip cleanly.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from squirrel_mcp.config import SquirrelConfig

IMAP_PORT = 3143
SMTP_PORT = 3025
TEST_EMAIL = "squirrel@example.com"
TEST_PASSWORD = "squirrelpass"
COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.test.yml"


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_port(host: str, port: int, deadline_s: float = 60.0) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if _port_open(host, port):
            return True
        time.sleep(1.0)
    return False


@pytest.fixture(scope="session")
def greenmail() -> SquirrelConfig:
    """Ensure a GreenMail server is up; yield a config pointed at it."""
    started_here = False

    if not _port_open("localhost", IMAP_PORT):
        if shutil.which("docker") is None:
            pytest.skip("Docker not available; skipping GreenMail integration tests")
        try:
            subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "greenmail"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:  # pragma: no cover - env dependent
            pytest.skip(f"Could not start GreenMail: {e.stderr}")
        started_here = True
        if not _wait_for_port("localhost", IMAP_PORT):
            pytest.skip("GreenMail did not become ready in time")

    # Give the SMTP listener a moment too.
    _wait_for_port("localhost", SMTP_PORT, deadline_s=15.0)

    config = SquirrelConfig(
        mail_provider="imap",
        mail_email=TEST_EMAIL,
        mail_username="squirrel",  # GreenMail's login is the local part
        mail_password=TEST_PASSWORD,
        imap_host="localhost",
        imap_port=IMAP_PORT,
        smtp_host="localhost",
        smtp_port=SMTP_PORT,
        imap_security="plain",
        smtp_security="plain",
        tls_verify=False,
    )

    yield config

    if started_here:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
            capture_output=True,
            text=True,
        )
