"""Tests for sanitizer — secret/PII redaction and body truncation."""

from ignite_trace.sanitizer import (
    sanitize_string,
    sanitize_dict,
    sanitize_value,
    truncate_body,
    REDACTED,
    PII_REDACTED,
)


class TestSanitizeString:
    def test_redacts_bearer_token(self):
        result = sanitize_string("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc123")
        assert REDACTED in result
        assert "eyJhbG" not in result

    def test_redacts_access_key(self):
        result = sanitize_string("access_key_live_abcdefghijklmnopqrstuvwxyz")
        assert REDACTED in result

    def test_redacts_secret_key(self):
        result = sanitize_string("sk_" + "test_1234567890abcdefghijklmn")
        assert REDACTED in result

    def test_redacts_ssn(self):
        result = sanitize_string("SSN is 123-45-6789")
        assert PII_REDACTED in result
        assert "123-45-6789" not in result

    def test_redacts_card_number(self):
        result = sanitize_string("Card: 4111-1111-1111-1111")
        assert PII_REDACTED in result

    def test_safe_string_unchanged(self):
        result = sanitize_string("This is a normal string")
        assert result == "This is a normal string"


class TestSanitizeDict:
    def test_redacts_sensitive_keys(self):
        result = sanitize_dict({
            "access_token": "real-token-value",
            "client_id": "safe-value",
        })
        assert result["access_token"] == REDACTED
        assert result["client_id"] == "safe-value"

    def test_redacts_password_key(self):
        result = sanitize_dict({"password": "hunter2", "username": "admin"})
        assert result["password"] == REDACTED
        assert result["username"] == "admin"

    def test_nested_dict_sanitization(self):
        result = sanitize_dict({
            "outer": {
                "secret_key": "should-be-redacted",
                "name": "safe",
            }
        })
        assert result["outer"]["secret_key"] == REDACTED
        assert result["outer"]["name"] == "safe"

    def test_list_values_sanitized(self):
        result = sanitize_dict({
            "headers": ["Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.something", "safe"]
        })
        assert REDACTED in result["headers"][0]
        assert result["headers"][1] == "safe"

    def test_sensitive_key_with_list_value_redacted(self):
        # Keys containing "token" are always fully redacted
        result = sanitize_dict({"tokens": ["value1", "value2"]})
        assert result["tokens"] == REDACTED


class TestSanitizeValue:
    def test_string(self):
        assert REDACTED in sanitize_value("sk_" + "live_abcdefghijklmnopqrstuvwxyz")

    def test_dict(self):
        result = sanitize_value({"secret": "x"})
        assert result["secret"] == REDACTED

    def test_list(self):
        result = sanitize_value(["safe", "sk_" + "live_abcdefghijklmnopqrstuvwxyz"])
        assert result[0] == "safe"
        assert REDACTED in result[1]

    def test_int_passthrough(self):
        assert sanitize_value(42) == 42

    def test_none_passthrough(self):
        assert sanitize_value(None) is None


class TestTruncateBody:
    def test_short_body_unchanged(self):
        assert truncate_body("short") == "short"

    def test_long_body_truncated(self):
        long_body = "x" * 2000
        result = truncate_body(long_body)
        assert len(result) == 1024
        assert result.endswith("...")

    def test_exact_length_unchanged(self):
        body = "x" * 1024
        assert truncate_body(body) == body
