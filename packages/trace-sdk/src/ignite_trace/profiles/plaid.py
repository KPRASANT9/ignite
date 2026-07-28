"""Plaid system profile — known endpoints, error codes, institutions.

Provides domain-specific context for Plaid exploration agents.
"""

SANDBOX_BASE_URL = "https://sandbox.plaid.com"

KNOWN_ENDPOINTS = {
    "link/token/create": {"method": "POST", "domain": "auth"},
    "item/public_token/exchange": {"method": "POST", "domain": "auth"},
    "item/get": {"method": "POST", "domain": "items"},
    "item/remove": {"method": "POST", "domain": "items"},
    "accounts/get": {"method": "POST", "domain": "accounts"},
    "accounts/balance/get": {"method": "POST", "domain": "balance"},
    "auth/get": {"method": "POST", "domain": "auth"},
    "transactions/sync": {"method": "POST", "domain": "transactions"},
    "transactions/get": {"method": "POST", "domain": "transactions"},
    "investments/holdings/get": {"method": "POST", "domain": "investments"},
    "investments/transactions/get": {"method": "POST", "domain": "investments"},
    "identity/get": {"method": "POST", "domain": "identity"},
    "identity/match": {"method": "POST", "domain": "identity"},
    "transfer/create": {"method": "POST", "domain": "transfer"},
    "transfer/get": {"method": "POST", "domain": "transfer"},
    "transfer/list": {"method": "POST", "domain": "transfer"},
    "transfer/cancel": {"method": "POST", "domain": "transfer"},
    "income/verification/create": {"method": "POST", "domain": "income"},
    "statements/list": {"method": "POST", "domain": "statements"},
    "statements/download": {"method": "POST", "domain": "statements"},
    "signal/evaluate": {"method": "POST", "domain": "signal"},
    "sandbox/item/fire_webhook": {"method": "POST", "domain": "sandbox"},
    "sandbox/item/reset_login": {"method": "POST", "domain": "sandbox"},
}

ERROR_CATEGORIES = {
    "INVALID_REQUEST": ["MISSING_FIELDS", "UNKNOWN_FIELDS", "INVALID_BODY"],
    "INVALID_INPUT": ["INVALID_ACCESS_TOKEN", "INVALID_PUBLIC_TOKEN", "INVALID_PRODUCT", "INVALID_ACCOUNT_ID"],
    "INSTITUTION_ERROR": ["INSTITUTION_DOWN", "INSTITUTION_NOT_RESPONDING", "INSTITUTION_NOT_AVAILABLE"],
    "RATE_LIMIT_EXCEEDED": ["TRANSACTIONS_LIMIT", "ACCOUNTS_LIMIT", "ITEM_LIMIT"],
    "API_ERROR": ["INTERNAL_SERVER_ERROR", "PLANNED_MAINTENANCE"],
    "ITEM_ERROR": [
        "ITEM_LOGIN_REQUIRED", "ITEM_NO_ERROR", "ITEM_NOT_FOUND",
        "ITEM_LOCKED", "ITEM_NO_VERIFICATION", "ITEM_PRODUCTS_NOT_READY",
    ],
    "ASSET_REPORT_ERROR": ["PRODUCT_NOT_ENABLED", "DATA_UNAVAILABLE"],
    "SANDBOX_ERROR": ["SANDBOX_PRODUCT_NOT_ENABLED"],
}

SANDBOX_INSTITUTIONS = {
    "ins_109508": "First Platypus Bank",
    "ins_109509": "First Gingham Credit Union",
    "ins_109510": "Tattersall Federal Credit Union",
    "ins_109511": "Tartan Bank",
    "ins_109512": "Houndstooth Bank",
}

SANDBOX_CREDENTIALS = {
    "user_good": "Normal user, all products available",
    "user_bad": "Triggers INVALID_CREDENTIALS",
    "user_mfa": "Triggers MFA (device, questions, selections, or code)",
    "user_custom": "Triggers custom sandbox scenarios",
    "user_transactions_dynamic": "Transactions update on each sync",
}

TRANSFER_STATES = [
    "pending", "posted", "settled", "returned",
    "failed", "cancelled", "reversed",
]
