"""Unit tests for tool helpers and error sanitizing."""

import pytest

from squirrel_mcp.error_handling import ValidationError
from squirrel_mcp.error_sanitizer import ErrorSanitizer
from squirrel_mcp.tools._common import as_str_list, require_confirm


def test_as_str_list_from_string():
    assert as_str_list("a@x.com, b@x.com") == ["a@x.com", "b@x.com"]


def test_as_str_list_from_list():
    assert as_str_list(["a", " b ", "", None]) == ["a", "b"]


def test_as_str_list_none():
    assert as_str_list(None) == []


def test_require_confirm_blocks_without_confirm():
    with pytest.raises(ValidationError, match="confirm=true"):
        require_confirm(False, "Sending mail")


def test_require_confirm_passes_with_confirm():
    require_confirm(True, "Sending mail")  # should not raise


def test_sanitizer_maps_auth_failure():
    msg = ErrorSanitizer.sanitize_message("b'[AUTHENTICATIONFAILED] Invalid credentials'")
    assert "Authentication failed" in msg


def test_sanitizer_redacts_password():
    msg = ErrorSanitizer.sanitize_message("login failed with password=hunter2 boo")
    assert "hunter2" not in msg
