"""Tests for M11-M15: institution clustering, health FSM, error-code ErrP, international variance."""

from ignite_trace.profiles.plaid import (
    AUTH_METHODS,
    COUNTRY_PAYMENT_RAILS,
    CREDENTIAL_ONLY_ERRORS,
    ENDPOINT_INTENTS,
    ERROR_CODE_CORRECTIONS,
    ERROR_RETRY_SAFETY,
    INSTITUTION_HEALTH_STATES,
    INSTITUTION_HEALTH_TRANSITIONS,
    KNOWN_ENDPOINTS,
    SANDBOX_INSTITUTIONS,
    get_institution_profile,
    get_products_for_country,
    is_structural_anomaly,
    is_valid_health_transition,
    lookup_errp_override,
)
from ignite_trace.span import SpanBuilder


class TestInstitutionClustering:
    """M11-M12: Institution profiles with clustering dimensions."""

    def test_sandbox_institution_count(self):
        """17 sandbox institutions across US, CA, GB."""
        assert len(SANDBOX_INSTITUTIONS) == 17

    def test_all_institutions_have_country(self):
        for inst_id, profile in SANDBOX_INSTITUTIONS.items():
            assert "country" in profile, f"{inst_id} missing country"

    def test_all_institutions_have_auth_method(self):
        for inst_id, profile in SANDBOX_INSTITUTIONS.items():
            assert "auth_method" in profile, f"{inst_id} missing auth_method"
            assert profile["auth_method"] in AUTH_METHODS, (
                f"{inst_id} has invalid auth_method: {profile['auth_method']}"
            )

    def test_all_institutions_have_type(self):
        for inst_id, profile in SANDBOX_INSTITUTIONS.items():
            assert "institution_type" in profile, f"{inst_id} missing institution_type"

    def test_us_standard_bank_full_products(self):
        """ins_109508 (First Platypus Bank) has full product suite."""
        bank = SANDBOX_INSTITUTIONS["ins_109508"]
        assert bank["country"] == "US"
        assert bank["auth_method"] == "credential"
        assert "transfer" in bank["products"]
        assert "signal" in bank["products"]

    def test_credit_union_narrower_products(self):
        """ins_109509 (credit union) lacks investments, transfer, signal."""
        cu = SANDBOX_INSTITUTIONS["ins_109509"]
        assert cu["institution_type"] == "credit_union"
        assert "transfer" not in cu["products"]
        assert "signal" not in cu["products"]

    def test_credit_union_extra_account_types(self):
        """Credit unions have money_market, cd, auto loan subtypes."""
        cu = SANDBOX_INSTITUTIONS["ins_109509"]
        assert "depository:money_market" in cu["account_types"]
        assert "depository:cd" in cu["account_types"]
        assert "loan:auto" in cu["account_types"]

    def test_oauth_institution_gapped_trace(self):
        """ins_127287 (OAuth bank) creates gapped trace shape."""
        oauth = SANDBOX_INSTITUTIONS["ins_127287"]
        assert oauth["auth_method"] == "oauth"

    def test_app2app_variant(self):
        """ins_132241 (App2App) uses deep link auth."""
        a2a = SANDBOX_INSTITUTIONS["ins_132241"]
        assert a2a["auth_method"] == "app2app"

    def test_four_auth_methods(self):
        assert set(AUTH_METHODS) == {"credential", "oauth", "app2app", "qr_code"}

    def test_get_institution_profile(self):
        profile = get_institution_profile("ins_109508")
        assert profile is not None
        assert profile["name"] == "First Platypus Bank"

    def test_get_institution_profile_unknown(self):
        assert get_institution_profile("ins_999999") is None

    def test_clustering_hierarchy_countries(self):
        """Institutions span US, CA, GB."""
        countries = {p["country"] for p in SANDBOX_INSTITUTIONS.values()}
        assert countries == {"US", "CA", "GB"}

    def test_clustering_hierarchy_institution_types(self):
        types = {p["institution_type"] for p in SANDBOX_INSTITUTIONS.values()}
        assert "bank" in types
        assert "credit_union" in types


class TestInstitutionHealthFSM:
    """M13: 4-state institution health FSM."""

    def test_four_health_states(self):
        assert len(INSTITUTION_HEALTH_STATES) == 4
        assert set(INSTITUTION_HEALTH_STATES) == {"healthy", "degraded", "down", "unsupported"}

    def test_healthy_to_degraded(self):
        assert is_valid_health_transition("healthy", "degraded")

    def test_degraded_to_healthy(self):
        """Degraded can recover to healthy."""
        assert is_valid_health_transition("degraded", "healthy")

    def test_degraded_to_down(self):
        assert is_valid_health_transition("degraded", "down")

    def test_down_to_degraded(self):
        """Down can recover to degraded."""
        assert is_valid_health_transition("down", "degraded")

    def test_down_to_unsupported(self):
        """Down → unsupported is the irreversible terminal transition."""
        assert is_valid_health_transition("down", "unsupported")

    def test_unsupported_is_terminal(self):
        """No transitions out of unsupported."""
        assert not is_valid_health_transition("unsupported", "healthy")
        assert not is_valid_health_transition("unsupported", "degraded")
        assert not is_valid_health_transition("unsupported", "down")

    def test_healthy_cannot_skip_to_down(self):
        """Must degrade before going down."""
        assert not is_valid_health_transition("healthy", "down")

    def test_healthy_cannot_go_unsupported(self):
        assert not is_valid_health_transition("healthy", "unsupported")

    def test_degraded_cannot_go_unsupported(self):
        """Only down → unsupported is valid."""
        assert not is_valid_health_transition("degraded", "unsupported")

    def test_sandbox_degraded_institution(self):
        inst = SANDBOX_INSTITUTIONS["ins_132363"]
        assert inst["health_status"] == "degraded"

    def test_sandbox_down_institution(self):
        inst = SANDBOX_INSTITUTIONS["ins_132361"]
        assert inst["health_status"] == "down"

    def test_sandbox_unsupported_institution(self):
        inst = SANDBOX_INSTITUTIONS["ins_133402"]
        assert inst["health_status"] == "unsupported"

    def test_transition_table_completeness(self):
        """Every health state has a transitions entry."""
        for state in INSTITUTION_HEALTH_STATES:
            assert state in INSTITUTION_HEALTH_TRANSITIONS


class TestErrorCodeErrP:
    """M15: Error-code-level ErrP overrides intent-class default."""

    def test_sixteen_error_codes_classified(self):
        assert len(ERROR_RETRY_SAFETY) == 16

    def test_safe_to_retry(self):
        for code in ["INTERNAL_SERVER_ERROR", "INSTITUTION_NOT_RESPONDING", "USER_INPUT_TIMEOUT"]:
            assert ERROR_RETRY_SAFETY[code] == "safe"

    def test_unsafe_to_retry(self):
        for code in ["INSTITUTION_DOWN", "ITEM_LOCKED"]:
            assert ERROR_RETRY_SAFETY[code] == "unsafe"

    def test_never_retry(self):
        never_codes = [
            "INVALID_CREDENTIALS", "INSUFFICIENT_CREDENTIALS", "COUNTRY_NOT_SUPPORTED",
            "ITEM_NOT_SUPPORTED", "MFA_NOT_SUPPORTED", "NO_ACCOUNTS",
            "USER_SETUP_REQUIRED", "INSTITUTION_NO_LONGER_SUPPORTED",
            "PAYMENT_INVALID_RECIPIENT",
        ]
        for code in never_codes:
            assert ERROR_RETRY_SAFETY[code] == "never", f"{code} should be never"

    def test_conditional_retry(self):
        for code in ["INVALID_MFA", "INVALID_SEND_METHOD"]:
            assert ERROR_RETRY_SAFETY[code] == "conditional"

    def test_item_locked_query_must_not_retry(self):
        """ITEM_LOCKED is a query that must NOT be retried — key M15 correction."""
        override = lookup_errp_override("ITEM_LOCKED")
        assert override == "log_escalate"

    def test_internal_server_error_mutation_can_retry(self):
        """INTERNAL_SERVER_ERROR CAN be retried even for mutations — key M15 correction."""
        override = lookup_errp_override("INTERNAL_SERVER_ERROR")
        assert override == "backoff_retry"

    def test_institution_down_should_wait(self):
        """INSTITUTION_DOWN → wait, don't retry (wastes rate budget)."""
        override = lookup_errp_override("INSTITUTION_DOWN")
        assert override == "log_escalate"

    def test_invalid_credentials_escalate(self):
        override = lookup_errp_override("INVALID_CREDENTIALS")
        assert override == "escalate_human"

    def test_unknown_error_code_returns_none(self):
        """Unknown error codes fall through to intent-class table."""
        assert lookup_errp_override("UNKNOWN_CODE") is None
        assert lookup_errp_override(None) is None

    def test_all_error_codes_have_corrections(self):
        """Every code in ERROR_RETRY_SAFETY has a matching ERROR_CODE_CORRECTIONS entry."""
        for code in ERROR_RETRY_SAFETY:
            assert code in ERROR_CODE_CORRECTIONS, f"{code} missing from ERROR_CODE_CORRECTIONS"

    def test_correction_values_are_valid(self):
        """All correction values are valid CorrectionAction enum values."""
        valid = {"backoff_retry", "reauth_retry", "log_escalate",
                 "idempotency_retry", "restart_flow", "escalate_human"}
        for code, action in ERROR_CODE_CORRECTIONS.items():
            assert action in valid, f"{code} → {action} is not a valid CorrectionAction"


class TestAuthMethodErrorProfiling:
    """M15: Auth method determines the expected error set."""

    def test_four_credential_only_errors(self):
        assert len(CREDENTIAL_ONLY_ERRORS) == 4

    def test_credential_only_errors_content(self):
        expected = {"INVALID_CREDENTIALS", "INVALID_MFA", "INSUFFICIENT_CREDENTIALS", "MFA_NOT_SUPPORTED"}
        assert set(CREDENTIAL_ONLY_ERRORS) == expected

    def test_oauth_structural_anomaly(self):
        """OAuth institution producing INVALID_CREDENTIALS = structural anomaly."""
        assert is_structural_anomaly("oauth", "INVALID_CREDENTIALS")
        assert is_structural_anomaly("oauth", "INVALID_MFA")

    def test_app2app_structural_anomaly(self):
        assert is_structural_anomaly("app2app", "INVALID_CREDENTIALS")

    def test_qr_code_structural_anomaly(self):
        assert is_structural_anomaly("qr_code", "INSUFFICIENT_CREDENTIALS")

    def test_credential_no_anomaly(self):
        """Credential institutions CAN produce these errors — not anomalous."""
        assert not is_structural_anomaly("credential", "INVALID_CREDENTIALS")
        assert not is_structural_anomaly("credential", "INVALID_MFA")

    def test_universal_errors_not_anomalous(self):
        """INSTITUTION_DOWN applies to all auth methods — never anomalous."""
        assert not is_structural_anomaly("oauth", "INSTITUTION_DOWN")
        assert not is_structural_anomaly("credential", "INSTITUTION_DOWN")


class TestInternationalVariance:
    """M14: Country-parameterized profile with payment rails."""

    def test_four_countries(self):
        assert len(COUNTRY_PAYMENT_RAILS) == 4
        assert set(COUNTRY_PAYMENT_RAILS.keys()) == {"US", "CA", "GB", "EU"}

    def test_us_ach_full_support(self):
        us = COUNTRY_PAYMENT_RAILS["US"]
        assert us["rail"] == "ach"
        assert us["transfer_supported"] is True
        assert us["signal_supported"] is True

    def test_canada_no_transfer(self):
        """Transfer/Signal are US-only (ACH). Canada uses EFT."""
        ca = COUNTRY_PAYMENT_RAILS["CA"]
        assert ca["rail"] == "eft"
        assert ca["transfer_supported"] is False

    def test_uk_faster_payments(self):
        gb = COUNTRY_PAYMENT_RAILS["GB"]
        assert gb["rail"] == "faster_payments"
        assert gb["transfer_supported"] is False

    def test_eu_sepa(self):
        eu = COUNTRY_PAYMENT_RAILS["EU"]
        assert eu["rail"] == "sepa"

    def test_canadian_institution_profile(self):
        ca = SANDBOX_INSTITUTIONS["ins_43"]
        assert ca["country"] == "CA"
        assert ca["currency"] == "CAD"
        assert ca["auth_number_format"] == "transit/institution/account"

    def test_uk_open_banking_mandatory_oauth(self):
        """UK institutions use OAuth (Open Banking PSD2/FCA mandate)."""
        uk = SANDBOX_INSTITUTIONS["ins_117650"]
        assert uk["country"] == "GB"
        assert uk["auth_method"] == "oauth"
        assert uk["regulatory_framework"] == "open_banking"

    def test_uk_qr_variant(self):
        qr = SANDBOX_INSTITUTIONS["ins_117181"]
        assert qr["auth_method"] == "qr_code"
        assert qr["regulatory_framework"] == "open_banking"

    def test_uk_time_limited_consent(self):
        uk = SANDBOX_INSTITUTIONS["ins_117650"]
        assert uk["consent_model"] == "time_limited"

    def test_get_products_for_country(self):
        us = get_products_for_country("US")
        assert us is not None
        assert us["transfer_supported"] is True

    def test_get_products_unknown_country(self):
        assert get_products_for_country("JP") is None


class TestInstitutionEndpoints:
    """M11-M14: Institution endpoints are registered and classified."""

    def test_institutions_get_by_id_registered(self):
        assert "institutions/get_by_id" in KNOWN_ENDPOINTS

    def test_institutions_search_registered(self):
        assert "institutions/search" in KNOWN_ENDPOINTS

    def test_institutions_get_by_id_is_query(self):
        assert ENDPOINT_INTENTS["institutions/get_by_id"] == "query"

    def test_institutions_search_is_query(self):
        assert ENDPOINT_INTENTS["institutions/search"] == "query"


class TestTraceShapeVariance:
    """M11: Auth method determines trace shape — credential (linear) vs OAuth (gapped)."""

    def test_credential_linear_trace(self):
        """Credential flow: single-context linear trace inside Plaid Link."""
        s = SpanBuilder(kind="auth_flow", target="plaid.link.credential_flow", sequence=1, trace_id="t")
        s.classify(request_intent="state_transition", response_outcome="success")
        s.observed(what_happened="CONSENT→CREDENTIALS→CONNECTED", what_learned="linear flow")
        d = s.to_dict()
        assert d["request_intent"] == "state_transition"
        assert d["response_outcome"] == "success"

    def test_oauth_gapped_trace(self):
        """OAuth flow: multi-context trace with unobservable gap during redirect."""
        s = SpanBuilder(kind="auth_flow", target="plaid.link.oauth_flow", sequence=1, trace_id="t")
        s.classify(request_intent="state_transition", response_outcome="success")
        s.observed(
            what_happened="CONSENT→OAUTH_REDIRECT→[GAP]→REDIRECT_BACK→CONNECTED",
            what_learned="unobservable gap during institution redirect",
        )
        d = s.to_dict()
        assert d["request_intent"] == "state_transition"

    def test_institution_health_degraded_span(self):
        """Degraded institution: L2 detects from rising error rate + latency."""
        s = SpanBuilder(kind="error_probe", target="institution_health_check", sequence=1, trace_id="t")
        s.classify(request_intent="query", response_outcome="error")
        s.observed(
            what_happened="INSTITUTION_NOT_RESPONDING intermittent",
            what_learned="error_rate trending up — possible degradation",
        )
        d = s.to_dict()
        assert d["response_outcome"] == "error"
