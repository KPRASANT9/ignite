"""Database modality profile — error taxonomy, intent classification, and ErrP overrides.

First non-API system profile, proving the universal trace surface works
cross-modality. Mirrors the Plaid profile structure: error codes -> retry
strategies, operation -> intent classification, structural anomaly detection.

Source: Honey DB Modality Product Spec, ProductExplorer trace-db-session-007.
"""

from __future__ import annotations


# --- Operation categories ---

DDL_OPERATIONS = frozenset({
    "CREATE TABLE", "CREATE INDEX", "ALTER TABLE", "DROP TABLE",
    "DROP INDEX", "CREATE VIEW", "DROP VIEW", "TRUNCATE",
    "CREATE TABLE AS SELECT",
})

DML_OPERATIONS = frozenset({
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT",
    "INSERT OR REPLACE", "INSERT OR IGNORE",
})

DQL_OPERATIONS = frozenset({
    "SELECT", "EXPLAIN", "EXPLAIN ANALYZE",
})

META_OPERATIONS = frozenset({
    "PRAGMA", "ANALYZE", "SHOW", "DESCRIBE",
    "SET", "VACUUM", "REINDEX",
})


# --- Risk levels by operation ---

OPERATION_RISK: dict[str, str] = {
    # DDL — schema changes
    "CREATE TABLE": "medium",
    "CREATE INDEX": "low",
    "CREATE VIEW": "low",
    "ALTER TABLE": "high",
    "DROP TABLE": "critical",
    "DROP INDEX": "medium",
    "DROP VIEW": "medium",
    "TRUNCATE": "critical",
    "CREATE TABLE AS SELECT": "high",
    # DML — data changes
    "INSERT": "low",
    "UPDATE": "medium",
    "DELETE": "high",
    "MERGE": "medium",
    "UPSERT": "medium",
    "INSERT OR REPLACE": "medium",
    "INSERT OR IGNORE": "low",
    # DQL — reads
    "SELECT": "low",
    "EXPLAIN": "low",
    "EXPLAIN ANALYZE": "low",
    # META
    "PRAGMA": "low",
    "ANALYZE": "low",
    "SHOW": "low",
    "DESCRIBE": "low",
    "SET": "medium",
    "VACUUM": "medium",
    "REINDEX": "medium",
}


# --- DB error taxonomy ---
# 5 families from Honey's spec, mapped to retry strategies.
# Mirrors Plaid M15 pattern: error code determines retry safety, not intent class.

DB_ERROR_CODES: dict[str, dict[str, str]] = {
    # Connection errors — safe to retry with backoff
    "connection_refused": {"family": "connection", "retry": "safe"},
    "connection_timeout": {"family": "connection", "retry": "safe"},
    "pool_exhausted": {"family": "connection", "retry": "safe"},
    "connection_reset": {"family": "connection", "retry": "safe"},
    # Constraint errors — never retry with same input
    "unique_violation": {"family": "constraint", "retry": "never"},
    "foreign_key_violation": {"family": "constraint", "retry": "never"},
    "check_constraint": {"family": "constraint", "retry": "never"},
    "not_null_violation": {"family": "constraint", "retry": "never"},
    # Lock errors — safe to retry immediately (DB already rolled back)
    "deadlock_detected": {"family": "lock", "retry": "safe"},
    "lock_timeout": {"family": "lock", "retry": "safe"},
    "serialization_failure": {"family": "lock", "retry": "safe"},
    # Resource errors — unsafe to retry, wait + monitor
    "disk_full": {"family": "resource", "retry": "unsafe"},
    "out_of_memory": {"family": "resource", "retry": "unsafe"},
    "too_many_connections": {"family": "resource", "retry": "unsafe"},
    # Syntax/auth errors — never retry
    "syntax_error": {"family": "syntax_auth", "retry": "never"},
    "permission_denied": {"family": "syntax_auth", "retry": "never"},
    "undefined_table": {"family": "syntax_auth", "retry": "never"},
    "undefined_column": {"family": "syntax_auth", "retry": "never"},
}


# --- ErrP correction overrides (error code -> CorrectionAction value) ---
# Same pattern as Plaid's ERROR_CODE_CORRECTIONS.

DB_ERROR_CODE_CORRECTIONS: dict[str, str] = {
    # Connection — backoff retry
    "connection_refused": "backoff_retry",
    "connection_timeout": "backoff_retry",
    "pool_exhausted": "backoff_retry",
    "connection_reset": "backoff_retry",
    # Constraint — escalate (never retry same input)
    "unique_violation": "escalate_human",
    "foreign_key_violation": "escalate_human",
    "check_constraint": "escalate_human",
    "not_null_violation": "escalate_human",
    # Lock — immediate retry (DB rolled back the txn)
    "deadlock_detected": "backoff_retry",
    "lock_timeout": "backoff_retry",
    "serialization_failure": "backoff_retry",
    # Resource — log + escalate (wait for external recovery)
    "disk_full": "log_escalate",
    "out_of_memory": "log_escalate",
    "too_many_connections": "log_escalate",
    # Syntax/auth — escalate (fix query/schema)
    "syntax_error": "escalate_human",
    "permission_denied": "escalate_human",
    "undefined_table": "escalate_human",
    "undefined_column": "escalate_human",
}


def lookup_db_errp_override(error_code: str) -> str | None:
    """Look up DB-specific ErrP correction override by error code.

    Returns CorrectionAction value string, or None if unknown error code.
    """
    return DB_ERROR_CODE_CORRECTIONS.get(error_code)


# --- Intent classification ---

def classify_db_intent(db_operation: str | None) -> str:
    """Classify a DB operation into a request intent.

    DB intent is almost always determinable from the operation keyword.
    Returns RequestIntent value string.
    """
    if db_operation is None:
        return "query"  # default

    op_upper = db_operation.strip().upper()

    # Check exact matches first
    if op_upper.startswith("SELECT") or op_upper.startswith("EXPLAIN"):
        return "query"
    if op_upper.startswith(("INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT")):
        return "mutation"
    if op_upper.startswith(("CREATE", "ALTER", "DROP", "TRUNCATE")):
        return "mutation"
    if op_upper.startswith(("PRAGMA", "ANALYZE", "SHOW", "DESCRIBE")):
        return "query"
    if op_upper.startswith(("SET", "VACUUM", "REINDEX")):
        return "config_change"

    return "query"  # safe default for unknown operations


def classify_db_risk(db_operation: str | None) -> str:
    """Classify DB operation risk level for Planner weighting.

    Returns: "low", "medium", "high", or "critical".
    """
    if db_operation is None:
        return "low"

    op_upper = db_operation.strip().upper()

    # Check exact match
    risk = OPERATION_RISK.get(op_upper)
    if risk is not None:
        return risk

    # Prefix matching for compound operations
    for known_op, risk_val in OPERATION_RISK.items():
        if op_upper.startswith(known_op):
            return risk_val

    return "low"


def infer_query_pattern(db_statement: str | None) -> str | None:
    """Infer query pattern from sanitized SQL statement.

    Returns a classification string for L2 query categorization,
    or None if the pattern cannot be inferred.
    """
    if db_statement is None:
        return None

    stmt = db_statement.upper()

    if "GROUP BY" in stmt:
        if "JOIN" in stmt:
            return "join_aggregate"
        return "aggregation"
    if "JOIN" in stmt:
        if "WHERE" in stmt:
            return "join_filter"
        return "join"
    if "WHERE" in stmt:
        # Distinguish point lookup from filtered scan
        if "= ?" in stmt or "= $" in stmt or "IN (" in stmt:
            return "point_lookup"
        return "filtered_scan"
    if stmt.strip().startswith("SELECT COUNT") or stmt.strip().startswith("SELECT EXISTS"):
        return "existence_check"
    if stmt.strip().startswith("SELECT"):
        return "full_scan"

    return None


# --- Structural anomaly detection ---

# Read-only connections should never produce write errors
READONLY_IMPOSSIBLE_ERRORS = frozenset({
    "unique_violation",
    "foreign_key_violation",
    "check_constraint",
    "not_null_violation",
})


def is_db_structural_anomaly(is_readonly: bool | None, error_code: str) -> bool:
    """Detect structurally impossible errors.

    A read-only connection producing a constraint violation is a structural
    anomaly, not a transient error. Same principle as OAuth + INVALID_CREDENTIALS
    in the Plaid profile.
    """
    if is_readonly is True and error_code in READONLY_IMPOSSIBLE_ERRORS:
        return True
    return False
