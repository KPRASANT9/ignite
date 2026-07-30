"""Plaid system profile — known endpoints, error codes, institutions.

Provides domain-specific context for Plaid exploration agents.
"""

from __future__ import annotations

SANDBOX_BASE_URL = "https://sandbox.plaid.com"

KNOWN_ENDPOINTS = {
    # Auth
    "link/token/create": {"method": "POST", "domain": "auth"},
    "item/public_token/exchange": {"method": "POST", "domain": "auth"},
    # Items
    "item/get": {"method": "POST", "domain": "items"},
    "item/remove": {"method": "POST", "domain": "items"},
    "item/webhook/update": {"method": "POST", "domain": "items"},
    # Accounts
    "accounts/get": {"method": "POST", "domain": "accounts"},
    "accounts/balance/get": {"method": "POST", "domain": "balance"},
    "auth/get": {"method": "POST", "domain": "auth"},
    # Transactions
    "transactions/sync": {"method": "POST", "domain": "transactions"},
    "transactions/get": {"method": "POST", "domain": "transactions"},
    "transactions/refresh": {"method": "POST", "domain": "transactions"},
    # Investments
    "investments/holdings/get": {"method": "POST", "domain": "investments"},
    "investments/transactions/get": {"method": "POST", "domain": "investments"},
    # Identity
    "identity/get": {"method": "POST", "domain": "identity"},
    "identity/match": {"method": "POST", "domain": "identity"},
    # Identity Verification (M9)
    "identity_verification/create": {"method": "POST", "domain": "identity_verification"},
    "identity_verification/get": {"method": "POST", "domain": "identity_verification"},
    "identity_verification/list": {"method": "POST", "domain": "identity_verification"},
    "identity_verification/retry": {"method": "POST", "domain": "identity_verification"},
    # Transfer (M6)
    "transfer/authorization/create": {"method": "POST", "domain": "transfer"},
    "transfer/create": {"method": "POST", "domain": "transfer"},
    "transfer/get": {"method": "POST", "domain": "transfer"},
    "transfer/list": {"method": "POST", "domain": "transfer"},
    "transfer/cancel": {"method": "POST", "domain": "transfer"},
    "transfer/event/sync": {"method": "POST", "domain": "transfer"},
    # Assets (M8)
    "asset_report/create": {"method": "POST", "domain": "assets"},
    "asset_report/get": {"method": "POST", "domain": "assets"},
    "asset_report/pdf/get": {"method": "POST", "domain": "assets"},
    "asset_report/refresh": {"method": "POST", "domain": "assets"},
    "asset_report/filter": {"method": "POST", "domain": "assets"},
    "asset_report/audit_copy/create": {"method": "POST", "domain": "assets"},
    # Income (M8)
    "income/verification/create": {"method": "POST", "domain": "income"},
    "income/verification/paystubs/get": {"method": "POST", "domain": "income"},
    "income/verification/documents/download": {"method": "POST", "domain": "income"},
    # Signal (M7)
    "signal/evaluate": {"method": "POST", "domain": "signal"},
    "signal/decision/report": {"method": "POST", "domain": "signal"},
    "signal/return/report": {"method": "POST", "domain": "signal"},
    # Statements
    "statements/list": {"method": "POST", "domain": "statements"},
    "statements/download": {"method": "POST", "domain": "statements"},
    # Institutions (M11-M14)
    "institutions/get_by_id": {"method": "POST", "domain": "institutions"},
    "institutions/search": {"method": "POST", "domain": "institutions"},
    # Sandbox
    "sandbox/item/fire_webhook": {"method": "POST", "domain": "sandbox"},
    "sandbox/item/reset_login": {"method": "POST", "domain": "sandbox"},
    "sandbox/transfer/simulate": {"method": "POST", "domain": "sandbox"},
    "sandbox/transfer/sweep/simulate": {"method": "POST", "domain": "sandbox"},
    "sandbox/transfer/refund/simulate": {"method": "POST", "domain": "sandbox"},
    "sandbox/income/fire_webhook": {"method": "POST", "domain": "sandbox"},
    "sandbox/transfer/fire_webhook": {"method": "POST", "domain": "sandbox"},
}

ERROR_CATEGORIES = {
    "INVALID_REQUEST": ["MISSING_FIELDS", "UNKNOWN_FIELDS", "INVALID_BODY"],
    "INVALID_INPUT": ["INVALID_ACCESS_TOKEN", "INVALID_PUBLIC_TOKEN", "INVALID_PRODUCT", "INVALID_ACCOUNT_ID"],
    "INSTITUTION_ERROR": ["INSTITUTION_DOWN", "INSTITUTION_NOT_RESPONDING", "INSTITUTION_NOT_AVAILABLE"],
    "RATE_LIMIT_EXCEEDED": [
        "ACCOUNTS_LIMIT", "ACCOUNTS_BALANCE_GET_LIMIT", "AUTH_LIMIT",
        "IDENTITY_LIMIT", "INVESTMENT_HOLDINGS_GET_LIMIT", "INVESTMENT_TRANSACTIONS_LIMIT",
        "ITEM_GET_LIMIT", "TRANSACTIONS_LIMIT", "TRANSACTIONS_REFRESH_LIMIT",
        "TRANSACTIONS_SYNC_LIMIT", "INSTITUTION_RATE_LIMIT", "RATE_LIMIT",
    ],
    "API_ERROR": ["INTERNAL_SERVER_ERROR", "PLANNED_MAINTENANCE"],
    "ITEM_ERROR": [
        "ITEM_LOGIN_REQUIRED", "ITEM_NO_ERROR", "ITEM_NOT_FOUND",
        "ITEM_LOCKED", "ITEM_NO_VERIFICATION", "ITEM_PRODUCTS_NOT_READY",
    ],
    "ASSET_REPORT_ERROR": ["PRODUCT_NOT_ENABLED", "DATA_UNAVAILABLE"],
    "SANDBOX_ERROR": ["SANDBOX_PRODUCT_NOT_ENABLED"],
}

# M11-M14: Sandbox institution profiles with clustering dimensions.
# Clustering hierarchy: Country → Auth Method → Institution Type → Product Support
SANDBOX_INSTITUTIONS = {
    # US — Standard banks (credential auth, full product suite)
    "ins_109508": {
        "name": "First Platypus Bank",
        "country": "US",
        "auth_method": "credential",
        "institution_type": "bank",
        "products": ["auth", "balance", "transactions", "identity", "assets",
                     "investments", "liabilities", "transfer", "signal"],
        "account_types": ["depository:checking", "depository:savings",
                          "credit:credit_card", "investment:401k"],
    },
    # US — Credit unions (credential auth, narrower product support)
    "ins_109509": {
        "name": "First Gingham Credit Union",
        "country": "US",
        "auth_method": "credential",
        "institution_type": "credit_union",
        "products": ["auth", "balance", "transactions", "identity"],
        "account_types": ["depository:checking", "depository:savings",
                          "depository:money_market", "depository:cd", "loan:auto"],
    },
    "ins_109510": {
        "name": "Tattersall Federal Credit Union",
        "country": "US",
        "auth_method": "credential",
        "institution_type": "credit_union",
        "products": ["auth", "balance", "transactions", "identity"],
    },
    "ins_109511": {
        "name": "Tartan Bank",
        "country": "US",
        "auth_method": "credential",
        "institution_type": "bank",
        "products": ["auth", "balance", "transactions", "identity"],
    },
    # US — Specialized (auth verification methods)
    "ins_109512": {
        "name": "Houndstooth Bank",
        "country": "US",
        "auth_method": "credential",
        "institution_type": "bank",
        "specialization": "auth_verification",
    },
    # US — OAuth institutions (M11: gapped trace shape)
    "ins_127287": {
        "name": "Platypus OAuth Bank",
        "country": "US",
        "auth_method": "oauth",
        "institution_type": "bank",
        "products": ["auth", "balance", "transactions", "identity"],
        "account_types": ["depository:checking", "depository:savings"],
    },
    # US — App2App OAuth variant
    "ins_132241": {
        "name": "First Platypus OAuth App2App Bank",
        "country": "US",
        "auth_method": "app2app",
        "institution_type": "bank",
    },
    # US — Specialized banks
    "ins_130016": {
        "name": "First Platypus Balance Bank",
        "country": "US",
        "auth_method": "credential",
        "institution_type": "bank",
        "specialization": "balance_focused",
    },
    "ins_138395": {
        "name": "First Platypus Limited Purpose",
        "country": "US",
        "auth_method": "credential",
        "institution_type": "bank",
        "specialization": "limited_purpose_checking",
    },
    "ins_135858": {
        "name": "Windowpane Bank",
        "country": "US",
        "auth_method": "credential",
        "institution_type": "bank",
        "specialization": "instant_micro",
    },
    # US — Unhealthy institutions (M13)
    "ins_132363": {
        "name": "Unhealthy Platypus Bank - Degraded",
        "country": "US",
        "auth_method": "credential",
        "institution_type": "bank",
        "health_status": "degraded",
    },
    "ins_132361": {
        "name": "Unhealthy Platypus Bank - Down",
        "country": "US",
        "auth_method": "credential",
        "institution_type": "bank",
        "health_status": "down",
    },
    "ins_133402": {
        "name": "Unsupported Platypus Bank",
        "country": "US",
        "auth_method": "credential",
        "institution_type": "bank",
        "health_status": "unsupported",
    },
    # Canada (M14)
    "ins_43": {
        "name": "Tartan-Dominion Bank of Canada",
        "country": "CA",
        "auth_method": "credential",
        "institution_type": "bank",
        "currency": "CAD",
        "products": ["auth", "balance", "transactions", "identity"],
        "auth_number_format": "transit/institution/account",
    },
    # UK (M14) — Open Banking (PSD2/FCA) mandatory OAuth
    "ins_117650": {
        "name": "Royal Bank of Plaid",
        "country": "GB",
        "auth_method": "oauth",
        "institution_type": "bank",
        "currency": "GBP",
        "regulatory_framework": "open_banking",
        "consent_model": "time_limited",
    },
    "ins_116834": {
        "name": "Flexible Platypus Open Banking",
        "country": "GB",
        "auth_method": "oauth",
        "institution_type": "bank",
        "currency": "GBP",
        "regulatory_framework": "open_banking",
    },
    "ins_117181": {
        "name": "Flexible Platypus Open Banking (QR)",
        "country": "GB",
        "auth_method": "qr_code",
        "institution_type": "bank",
        "currency": "GBP",
        "regulatory_framework": "open_banking",
    },
}

# M13: Institution health is a 4-state FSM.
# healthy ↔ degraded ↔ down (reversible) → unsupported (irreversible terminal)
INSTITUTION_HEALTH_STATES = ["healthy", "degraded", "down", "unsupported"]

# M11: Auth methods determine trace shape and available error set.
# credential = linear trace (single context)
# oauth = gapped trace (unobservable gap during institution redirect)
# app2app = gapped trace (deep link to banking app)
# qr_code = gapped trace (cross-device QR scan)
AUTH_METHODS = ["credential", "oauth", "app2app", "qr_code"]

SANDBOX_CREDENTIALS = {
    "user_good": "Normal user, all products available",
    "user_bad": "Triggers INVALID_CREDENTIALS",
    "user_mfa": "Triggers MFA (device, questions, selections, or code)",
    "user_custom": "Triggers custom sandbox scenarios",
    "user_transactions_dynamic": "Transactions update on each sync",
}

# M6: 7-state ACH FSM with credit/debit divergence.
# Debit: pending→posted→settled→funds_available (+cancelled/failed/returned)
# Credit: pending→posted→settled (+cancelled/failed/returned, no funds_available)
# Cancellation only from pending. Returns up to 60 days for consumer debits.
TRANSFER_STATES = [
    "pending", "posted", "settled", "funds_available",
    "cancelled", "failed", "returned",
]

# Transfer event types from /transfer/event/sync (M6)
TRANSFER_EVENT_TYPES = [
    "pending", "cancelled", "failed", "posted", "settled",
    "funds_available", "returned",
    # Sweep events (linked but independent FSM)
    "swept", "swept_settled", "return_swept",
]

# v0.2: Per-endpoint intent classification.
# HTTP method is unreliable for Plaid (POST for everything including reads).
# Classification is from endpoint semantics, validated against ProductExplorer
# trace-intent-taxonomy-004 (34 interactions, 0.93 avg confidence).
ENDPOINT_INTENTS = {
    # Auth
    "link/token/create": "state_transition",
    "item/public_token/exchange": "state_transition",
    # Items
    "item/get": "query",
    "item/remove": "mutation",
    "item/webhook/update": "config_change",
    # Accounts
    "accounts/get": "query",
    "accounts/balance/get": "query",
    "auth/get": "query",
    # Transactions
    "transactions/sync": "query",
    "transactions/get": "query",
    "transactions/refresh": "mutation",
    # Investments
    "investments/holdings/get": "query",
    "investments/transactions/get": "query",
    # Identity
    "identity/get": "query",
    "identity/match": "query",
    # Identity Verification (M9) — multi-step orchestrated flow
    "identity_verification/create": "state_transition",  # lifecycle_start: creates IDV session
    "identity_verification/get": "query",
    "identity_verification/list": "query",
    "identity_verification/retry": "state_transition",  # lifecycle_start: new linked attempt
    # Transfer (M6) — 7-state ACH FSM
    "transfer/authorization/create": "decision",  # gate judgment with rationale codes
    "transfer/create": "mutation",
    "transfer/get": "query",
    "transfer/list": "query",
    "transfer/cancel": "mutation",
    "transfer/event/sync": "query",
    # Assets (M8) — async report pattern
    "asset_report/create": "state_transition",  # lifecycle_start: initiates async report
    "asset_report/get": "query",  # may return async_pending outcome
    "asset_report/pdf/get": "query",
    "asset_report/refresh": "state_transition",  # lifecycle_start: creates new report version
    "asset_report/filter": "mutation",
    "asset_report/audit_copy/create": "mutation",
    # Income (M8) — async report pattern
    "income/verification/create": "state_transition",  # lifecycle_start
    "income/verification/paystubs/get": "query",
    "income/verification/documents/download": "query",
    # Signal (M7) — opaque scoring archetype
    "signal/evaluate": "query",  # input→score→decision (not FSM)
    "signal/decision/report": "mutation",  # Loop 3 feedback: outcome→model improvement
    "signal/return/report": "mutation",  # actual ACH return data fed back
    # Statements
    "statements/list": "query",
    "statements/download": "query",
    # Institutions (M11-M14)
    "institutions/get_by_id": "query",
    "institutions/search": "query",
    # Sandbox
    "sandbox/item/fire_webhook": "mutation",
    "sandbox/item/reset_login": "state_transition",
    "sandbox/transfer/simulate": "mutation",
    "sandbox/transfer/sweep/simulate": "mutation",
    "sandbox/transfer/refund/simulate": "mutation",
    "sandbox/income/fire_webhook": "mutation",
    "sandbox/transfer/fire_webhook": "mutation",
}

# M10: Per-endpoint rate limits (per item/min, per client/min).
# No Retry-After header — operator must infer backoff from error_code + these thresholds.
RATE_LIMITS = {
    "accounts/get": {"per_item_min": 15, "per_client_min": 15000},
    "accounts/balance/get": {"per_item_min": 5, "per_item_hour": 30, "per_client_min": 1200},
    "auth/get": {"per_item_min": 15, "per_client_min": 12000},
    "identity/get": {"per_item_min": 15, "per_client_min": 2000},
    "investments/holdings/get": {"per_item_min": 15, "per_client_min": 2000},
    "investments/transactions/get": {"per_item_min": 30, "per_client_min": 20000},
    "item/get": {"per_item_min": 15, "per_client_min": 5000},
    "transactions/get": {"per_item_min": 30, "per_client_min": 20000},
    "transactions/sync": {"per_item_min": 50, "per_client_min": 2500, "per_client_min_empty_cursor": 500},
    "transactions/refresh": {"per_item_min": 2, "per_item_hour": 120, "per_item_day": 2880, "per_client_min": 100},
}

# M10: Rate limit error code → backoff strategy mapping for ErrP loop.
RATE_LIMIT_ERRORS = {
    "ACCOUNTS_LIMIT": "accounts/get",
    "ACCOUNTS_BALANCE_GET_LIMIT": "accounts/balance/get",
    "AUTH_LIMIT": "auth/get",
    "IDENTITY_LIMIT": "identity/get",
    "INVESTMENT_HOLDINGS_GET_LIMIT": "investments/holdings/get",
    "INVESTMENT_TRANSACTIONS_LIMIT": "investments/transactions/get",
    "ITEM_GET_LIMIT": "item/get",
    "TRANSACTIONS_LIMIT": "transactions/get",
    "TRANSACTIONS_REFRESH_LIMIT": "transactions/refresh",
    "TRANSACTIONS_SYNC_LIMIT": "transactions/sync",
    "INSTITUTION_RATE_LIMIT": None,  # institution-specific, varies
    "RATE_LIMIT": None,  # catch-all
}

# M15: Error retry safety classification.
# The intent-class retry heuristic (retry queries, don't retry mutations) is INSUFFICIENT.
# Error code determines retry safety, not intent class.
# ITEM_LOCKED is a query that must NOT be retried. INTERNAL_SERVER_ERROR CAN be retried.
ERROR_RETRY_SAFETY = {
    # SAFE TO RETRY (transient, may resolve with backoff)
    "INTERNAL_SERVER_ERROR": "safe",
    "INSTITUTION_NOT_RESPONDING": "safe",
    "USER_INPUT_TIMEOUT": "safe",
    # UNSAFE TO RETRY (retrying is futile or harmful — wait for external recovery)
    "INSTITUTION_DOWN": "unsafe",
    "ITEM_LOCKED": "unsafe",
    # NEVER RETRY (requires human intervention or config change)
    "INVALID_CREDENTIALS": "never",
    "INSUFFICIENT_CREDENTIALS": "never",
    "COUNTRY_NOT_SUPPORTED": "never",
    "ITEM_NOT_SUPPORTED": "never",
    "MFA_NOT_SUPPORTED": "never",
    "NO_ACCOUNTS": "never",
    "USER_SETUP_REQUIRED": "never",
    "INSTITUTION_NO_LONGER_SUPPORTED": "never",
    "PAYMENT_INVALID_RECIPIENT": "never",
    # CONDITIONAL RETRY (retry with DIFFERENT input, not the same)
    "INVALID_MFA": "conditional",
    "INVALID_SEND_METHOD": "conditional",
}

# M15: Errors that are IMPOSSIBLE under OAuth (credential-flow-only).
# If an OAuth institution produces these, it is a structural anomaly.
CREDENTIAL_ONLY_ERRORS = [
    "INVALID_CREDENTIALS",
    "INVALID_MFA",
    "INSUFFICIENT_CREDENTIALS",
    "MFA_NOT_SUPPORTED",
]

# M14: Country-specific payment rails.
# Transfer/Signal are US-only (ACH network). Other countries have different rails.
COUNTRY_PAYMENT_RAILS = {
    "US": {"rail": "ach", "transfer_supported": True, "signal_supported": True},
    "CA": {"rail": "eft", "transfer_supported": False, "signal_supported": False},
    "GB": {"rail": "faster_payments", "transfer_supported": False, "signal_supported": False},
    "EU": {"rail": "sepa", "transfer_supported": False, "signal_supported": False},
}

# --- M13: Institution health FSM transitions ---
# healthy ↔ degraded ↔ down (reversible), down → unsupported (irreversible terminal)

INSTITUTION_HEALTH_TRANSITIONS: dict[str, list[str]] = {
    "healthy": ["degraded"],
    "degraded": ["healthy", "down"],
    "down": ["degraded", "unsupported"],
    "unsupported": [],  # terminal — no transitions out
}


def is_valid_health_transition(from_state: str, to_state: str) -> bool:
    """Check if an institution health state transition is valid."""
    return to_state in INSTITUTION_HEALTH_TRANSITIONS.get(from_state, [])


# --- M15: Error-code-aware ErrP override ---

# Maps error codes to CorrectionAction values (same enum as parser.models).
# Error code OVERRIDES the intent-class default when present.
ERROR_CODE_CORRECTIONS: dict[str, str] = {
    # Safe to retry → backoff_retry
    "INTERNAL_SERVER_ERROR": "backoff_retry",
    "INSTITUTION_NOT_RESPONDING": "backoff_retry",
    "USER_INPUT_TIMEOUT": "backoff_retry",
    # Unsafe to retry → log_escalate (wait for external recovery)
    "INSTITUTION_DOWN": "log_escalate",
    "ITEM_LOCKED": "log_escalate",
    # Never retry → escalate_human
    "INVALID_CREDENTIALS": "escalate_human",
    "INSUFFICIENT_CREDENTIALS": "escalate_human",
    "COUNTRY_NOT_SUPPORTED": "escalate_human",
    "ITEM_NOT_SUPPORTED": "escalate_human",
    "MFA_NOT_SUPPORTED": "escalate_human",
    "NO_ACCOUNTS": "escalate_human",
    "USER_SETUP_REQUIRED": "escalate_human",
    "INSTITUTION_NO_LONGER_SUPPORTED": "escalate_human",
    "PAYMENT_INVALID_RECIPIENT": "escalate_human",
    # Conditional retry → reauth_retry (retry with different input)
    "INVALID_MFA": "reauth_retry",
    "INVALID_SEND_METHOD": "reauth_retry",
}


def lookup_errp_override(error_code: str | None) -> str | None:
    """Look up error-code-level ErrP correction, bypassing intent-class default.

    Returns a CorrectionAction value string if the error code has an override,
    or None to fall back to the (intent, outcome) table.
    """
    if error_code is None:
        return None
    return ERROR_CODE_CORRECTIONS.get(error_code)


def is_structural_anomaly(auth_method: str, error_code: str) -> bool:
    """Check if an error code is structurally impossible for the given auth method.

    OAuth/App2App/QR institutions cannot produce credential-flow-only errors.
    If they do, it's a structural anomaly requiring investigation, not retry.
    """
    if auth_method in ("oauth", "app2app", "qr_code"):
        return error_code in CREDENTIAL_ONLY_ERRORS
    return False


def get_institution_profile(institution_id: str) -> dict | None:
    """Look up a sandbox institution's clustering profile."""
    return SANDBOX_INSTITUTIONS.get(institution_id)


def get_products_for_country(country: str) -> dict | None:
    """Look up payment rail and product availability for a country."""
    return COUNTRY_PAYMENT_RAILS.get(country)
