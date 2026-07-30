"""Tests for web modality profile — error taxonomy, intent/risk classification, privacy masking."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_trace.profiles.web import (
    classify_web_intent,
    classify_web_risk,
    is_web_structural_anomaly,
    lookup_web_errp_override,
    WEB_ERROR_CODES,
    WEB_ERROR_CODE_CORRECTIONS,
)
from ignite_trace.extensions.web_ext import WebExt, mask_sensitive_input
from ignite_trace.sanitizer import sanitize_web_input


# --- Error taxonomy ---


class TestWebErrorTaxonomy:
    def test_all_error_codes_have_corrections(self):
        for code in WEB_ERROR_CODES:
            assert code in WEB_ERROR_CODE_CORRECTIONS, f"Missing correction for {code}"

    def test_element_not_found_conditional_retry(self):
        info = WEB_ERROR_CODES["element_not_found"]
        assert info["retry"] == "conditional"
        assert lookup_web_errp_override("element_not_found") == "idempotency_retry"

    def test_session_expired_never_retry(self):
        info = WEB_ERROR_CODES["session_expired"]
        assert info["retry"] == "never"
        assert lookup_web_errp_override("session_expired") == "restart_flow"

    def test_browser_crashed_unsafe(self):
        assert WEB_ERROR_CODES["browser_crashed"]["retry"] == "unsafe"
        assert lookup_web_errp_override("browser_crashed") == "log_escalate"

    def test_unknown_code_returns_none(self):
        assert lookup_web_errp_override("unknown_error") is None

    def test_navigation_timeout_backoff(self):
        assert lookup_web_errp_override("navigation_timeout") == "backoff_retry"


# --- Intent classification ---


class TestWebIntentClassification:
    def test_link_is_query(self):
        assert classify_web_intent(element_role="link") == "query"

    def test_menuitem_is_query(self):
        assert classify_web_intent(element_role="menuitem") == "query"

    def test_button_is_mutation(self):
        assert classify_web_intent(element_role="button") == "mutation"

    def test_textbox_is_mutation(self):
        assert classify_web_intent(element_role="textbox") == "mutation"

    def test_navigate_action_is_query(self):
        assert classify_web_intent(action="navigate") == "query"

    def test_scroll_is_query(self):
        assert classify_web_intent(action="scroll") == "query"

    def test_type_action_is_mutation(self):
        assert classify_web_intent(action="type") == "mutation"

    def test_submit_is_mutation(self):
        assert classify_web_intent(action="submit") == "mutation"

    def test_role_takes_precedence_over_action(self):
        # link role + click action → query (role wins)
        assert classify_web_intent(element_role="link", action="click") == "query"

    def test_default_is_query(self):
        assert classify_web_intent() == "query"


# --- Risk classification ---


class TestWebRiskClassification:
    def test_button_medium(self):
        assert classify_web_risk(element_role="button") == "medium"

    def test_submit_action_high(self):
        assert classify_web_risk(action="submit") == "high"

    def test_navigate_low(self):
        assert classify_web_risk(action="navigate") == "low"

    def test_observe_low(self):
        assert classify_web_risk(action="observe") == "low"

    def test_default_low(self):
        assert classify_web_risk() == "low"


# --- Structural anomaly ---


class TestWebStructuralAnomaly:
    def test_observe_with_stale_element(self):
        assert is_web_structural_anomaly("observe", "stale_element") is True

    def test_click_with_stale_element_not_anomalous(self):
        assert is_web_structural_anomaly("click", "stale_element") is False

    def test_observe_with_navigation_error_not_anomalous(self):
        assert is_web_structural_anomaly("observe", "navigation_timeout") is False


# --- Privacy masking (WebExt level) ---


class TestPrivacyMasking:
    def test_mask_password_by_type(self):
        assert mask_sensitive_input("secret123", input_type="password") == "***"

    def test_mask_password_by_name(self):
        assert mask_sensitive_input("secret123", element_name="password") == "***"

    def test_mask_ssn_by_name(self):
        assert mask_sensitive_input("123-45-6789", element_name="ssn_field") == "***"

    def test_mask_card_by_name(self):
        assert mask_sensitive_input("4111111111111111", element_name="card_number") == "***"

    def test_no_mask_username(self):
        assert mask_sensitive_input("test@example.com", element_name="username") == "test@example.com"

    def test_none_returns_none(self):
        assert mask_sensitive_input(None, element_name="password") is None

    def test_mask_credit_card_name(self):
        assert mask_sensitive_input("4111", element_name="credit_card_input") == "***"


# --- Sanitizer level privacy ---


class TestSanitizerWebInput:
    def test_sanitize_password(self):
        assert sanitize_web_input("mypassword", input_type="password") == "***"

    def test_sanitize_ssn_name(self):
        assert sanitize_web_input("123-45-6789", element_name="social_security_number") == "***"

    def test_sanitize_normal_input(self):
        assert sanitize_web_input("hello", element_name="search") == "hello"

    def test_sanitize_none(self):
        assert sanitize_web_input(None) is None


# --- WebExt roundtrip with new fields ---


class TestWebExtEnhanced:
    def test_new_fields_roundtrip(self):
        original = WebExt(
            page_url="https://example.com",
            page_title="Test",
            action="type",
            element_role="textbox",
            element_name="username",
            input_value="test@example.com",
            input_type="text",
            snapshot_hash="abc123",
            delta_from="web-span-001",
        )
        d = original.to_dict()
        restored = WebExt.from_dict(d)
        assert restored.element_name == "username"
        assert restored.input_value == "test@example.com"
        assert restored.input_type == "text"
        assert restored.snapshot_hash == "abc123"
        assert restored.delta_from == "web-span-001"

    def test_password_field_masked_in_trace(self):
        """Demonstrate the privacy workflow: mask before constructing WebExt."""
        raw_password = "s3cret!"
        masked = mask_sensitive_input(raw_password, element_name="password", input_type="password")
        ext = WebExt(
            action="type",
            element_role="textbox",
            element_name="password",
            input_value=masked,
            input_type="password",
        )
        d = ext.to_dict()
        assert d["input_value"] == "***"
        assert d["input_type"] == "password"
