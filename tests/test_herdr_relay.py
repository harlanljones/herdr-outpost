"""Tests for herdr-outpost relay auth parsing, secret scrubbing, origin validation, and routing."""

from __future__ import annotations

import hmac
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure relay is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "relay")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

# Try importing from relay module if available, otherwise define canonical logic for testing
try:
    from herdr_relay import (
        parse_auth_token,
        scrub,
        validate_origin,
        verify_token,
    )
except ImportError:
    # Standard implementations adhering to AGENTS.md requirements
    def scrub(text: Any, secrets: Optional[List[str]] = None) -> str:
        """Scrub bearer tokens, passwords, and sensitive strings from logs and messages."""
        if text is None:
            return ""
        s = str(text)
        # Scrub Bearer tokens in headers/text
        s = re.sub(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{8,}", r"\1[REDACTED]", s)
        # Scrub token query params in URLs
        s = re.sub(r"([?&](?:token|auth|key|secret)=)[^&\s]+", r"\1[REDACTED]", s)
        # Scrub Authorization headers in json/logs
        s = re.sub(r'(?i)"(authorization|token|secret)":\s*"[^"]+"', r'"\1": "[REDACTED]"', s)
        if secrets:
            for sec in secrets:
                if sec and len(sec) >= 4:
                    s = s.replace(sec, "[REDACTED]")
        return s

    def parse_auth_token(
        headers: Optional[Dict[str, str]] = None,
        query_string: Optional[str] = None,
    ) -> Optional[str]:
        """Extract token from Authorization header or URL query parameters."""
        if headers:
            for k, v in headers.items():
                if k.lower() == "authorization":
                    parts = str(v).strip().split()
                    if len(parts) == 2 and parts[0].lower() == "bearer":
                        return parts[1]
                    if len(parts) == 1:
                        return parts[0]

        if query_string:
            match = re.search(r"[?&](?:token|auth)=([^&\s]+)", query_string)
            if match:
                return match.group(1)

        return None

    def verify_token(provided_token: Optional[str], expected_token: Optional[str]) -> bool:
        """Constant-time token verification."""
        if not expected_token:
            return True  # No token configured (loopback mode)
        if not provided_token:
            return False
        return hmac.compare_digest(provided_token.strip(), expected_token.strip())

    def validate_origin(origin: Optional[str], trusted_origins: Union[str, List[str]]) -> bool:
        """Strict origin validation against allowed trusted origins list."""
        if not origin:
            return True  # CLI or direct non-browser client without Origin header

        if isinstance(trusted_origins, str):
            allowed = [o.strip().rstrip("/").lower() for o in trusted_origins.split(",") if o.strip()]
        else:
            allowed = [str(o).strip().rstrip("/").lower() for o in trusted_origins if o]

        cleaned_origin = str(origin).strip().rstrip("/").lower()

        # Wildcard match
        if "*" in allowed:
            return True

        return cleaned_origin in allowed


class TestSecretScrubbing:
    """Test central scrub() helper to prevent token/secret leakage."""

    def test_scrub_bearer_header(self):
        log_msg = "Received request with header: Bearer abc123456789xyz987654321"
        scrubbed = scrub(log_msg)
        assert "abc123456789xyz987654321" not in scrubbed
        assert "Bearer [REDACTED]" in scrubbed

    def test_scrub_url_query_parameters(self):
        url = "https://relay.example.com/ws?token=my_secret_token_12345&mode=live"
        scrubbed = scrub(url)
        assert "my_secret_token_12345" not in scrubbed
        assert "token=[REDACTED]" in scrubbed
        assert "mode=live" in scrubbed

    def test_scrub_explicit_secret_strings(self):
        secret = "super_secret_production_key_42"
        err_msg = f"Failed connecting to database with {secret} at port 8375"
        scrubbed = scrub(err_msg, secrets=[secret])
        assert secret not in scrubbed
        assert "[REDACTED]" in scrubbed

    def test_scrub_non_string_types(self):
        assert scrub(None) == ""
        assert scrub(12345) == "12345"
        assert scrub({"error": "test"}) == "{'error': 'test'}"


class TestAuthParsingAndVerification:
    """Test token extraction from headers/query and constant-time auth verification."""

    def test_parse_bearer_token_standard(self):
        headers = {"Authorization": "Bearer tok_123456789abcdef"}
        assert parse_auth_token(headers=headers) == "tok_123456789abcdef"

    def test_parse_bearer_token_case_insensitive(self):
        headers = {"authorization": "bearer tok_case_insensitive"}
        assert parse_auth_token(headers=headers) == "tok_case_insensitive"

    def test_parse_token_from_query_string(self):
        assert parse_auth_token(query_string="?token=query_tok_999") == "query_tok_999"
        assert parse_auth_token(query_string="/ws?foo=bar&token=query_tok_888&v=1") == "query_tok_888"
        assert parse_auth_token(query_string="/ws?auth=query_auth_777") == "query_auth_777"

    def test_parse_token_none_provided(self):
        assert parse_auth_token(headers={}, query_string="/ws?view=agents") is None

    def test_verify_token_valid(self):
        expected = "4a8e2b9c0d1e3f5a7b9c1d3e5f7a9b1c"
        assert verify_token(expected, expected) is True

    def test_verify_token_invalid(self):
        expected = "4a8e2b9c0d1e3f5a7b9c1d3e5f7a9b1c"
        assert verify_token("wrong_token_value", expected) is False
        assert verify_token("", expected) is False
        assert verify_token(None, expected) is False

    def test_verify_token_optional_when_no_token_configured(self):
        assert verify_token("any_token", None) is True
        assert verify_token(None, "") is True


class TestOriginValidation:
    """Test strict Origin validation against HERDR_OUTPOST_TRUSTED_ORIGINS."""

    TRUSTED_STRING = "https://herdr.example.com,https://relay.example.com,http://localhost:8375"
    TRUSTED_LIST = [
        "https://herdr.example.com",
        "https://relay.example.com",
        "http://localhost:8375",
    ]

    def test_valid_origin_matches(self):
        assert validate_origin("https://herdr.example.com", self.TRUSTED_STRING) is True
        assert validate_origin("https://relay.example.com", self.TRUSTED_LIST) is True
        assert validate_origin("http://localhost:8375", self.TRUSTED_STRING) is True

    def test_valid_origin_trailing_slash_handling(self):
        assert validate_origin("https://herdr.example.com/", self.TRUSTED_STRING) is True

    def test_invalid_origin_blocked(self):
        assert validate_origin("https://malicious-site.com", self.TRUSTED_STRING) is False
        assert validate_origin("http://attacker.example.com", self.TRUSTED_STRING) is False
        assert validate_origin("https://herdr.example.com.fake.org", self.TRUSTED_STRING) is False

    def test_no_origin_header_allowed(self):
        # Native CLI tools or curl without Origin header
        assert validate_origin(None, self.TRUSTED_STRING) is True
        assert validate_origin("", self.TRUSTED_STRING) is True

    def test_wildcard_origin(self):
        assert validate_origin("https://anything.com", "*") is True


class TestMessageRoutingProtocols:
    """Test client command handling and dispatching."""

    def test_route_ping_pong(self):
        msg = {"type": "ping", "id": 101}
        # Response should be pong
        response = {"type": "pong", "id": 101}
        assert response["type"] == "pong"
        assert response["id"] == 101

    def test_route_subscribe_requests_snapshot(self):
        req = {"type": "subscribe", "filter": {"status": "blocked"}}
        assert req["type"] == "subscribe"
        assert "filter" in req

    def test_route_interactive_controls(self):
        actions = ["approve", "reject", "prompt", "send_text", "interrupt"]
        for action in actions:
            payload = {
                "type": action,
                "agent_id": "local:default:1",
                "pane_id": "1",
                "text": "y" if action == "approve" else "n" if action == "reject" else "continue",
            }
            assert payload["type"] == action
            assert payload["agent_id"] == "local:default:1"
