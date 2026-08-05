"""Tests for IGNITE DB profile — error taxonomy, intent classification, ErrP overrides.

Mirrors the structure of test_plaid_m11_m15.py. Validates:
- DB error code -> retry strategy mapping (16 error codes × 5 families)
- Intent classification from db_operation
- Risk level classification
- Query pattern inference
- Structural anomaly detection
- DbExt query_pattern field roundtrip
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_trace.profiles.db import (
    DB_ERROR_CODES,
    DB_ERROR_CODE_CORRECTIONS,
    DDL_OPERATIONS,
    DML_OPERATIONS,
    DQL_OPERATIONS,
    META_OPERATIONS,
    OPERATION_RISK,
    READONLY_IMPOSSIBLE_ERRORS,
    classify_db_intent,
    classify_db_risk,
    infer_query_pattern,
    is_db_structural_anomaly,
    lookup_db_errp_override,
)
from ignite_trace.extensions.db_ext import DbExt


# --- Error taxonomy ---


class TestDbErrorTaxonomy:
    def test_all_error_codes_have_corrections(self):
        for code in DB_ERROR_CODES:
            assert code in DB_ERROR_CODE_CORRECTIONS, f"Missing correction for {code}"

    def test_connection_errors_safe_to_retry(self):
        connection_codes = [c for c, v in DB_ERROR_CODES.items() if v["family"] == "connection"]
        assert len(connection_codes) == 4
        for code in connection_codes:
            assert DB_ERROR_CODES[code]["retry"] == "safe"

    def test_constraint_errors_never_retry(self):
        constraint_codes = [c for c, v in DB_ERROR_CODES.items() if v["family"] == "constraint"]
        assert len(constraint_codes) == 4
        for code in constraint_codes:
            assert DB_ERROR_CODES[code]["retry"] == "never"

    def test_lock_errors_safe_to_retry(self):
        lock_codes = [c for c, v in DB_ERROR_CODES.items() if v["family"] == "lock"]
        assert len(lock_codes) == 3
        for code in lock_codes:
            assert DB_ERROR_CODES[code]["retry"] == "safe"

    def test_resource_errors_unsafe(self):
        resource_codes = [c for c, v in DB_ERROR_CODES.items() if v["family"] == "resource"]
        assert len(resource_codes) == 3
        for code in resource_codes:
            assert DB_ERROR_CODES[code]["retry"] == "unsafe"

    def test_syntax_auth_errors_never_retry(self):
        syntax_codes = [c for c, v in DB_ERROR_CODES.items() if v["family"] == "syntax_auth"]
        assert len(syntax_codes) == 4
        for code in syntax_codes:
            assert DB_ERROR_CODES[code]["retry"] == "never"

    def test_total_error_codes(self):
        assert len(DB_ERROR_CODES) == 18


# --- ErrP overrides ---


class TestDbErrPOverride:
    def test_deadlock_backoff_retry(self):
        assert lookup_db_errp_override("deadlock_detected") == "backoff_retry"

    def test_unique_violation_escalate(self):
        assert lookup_db_errp_override("unique_violation") == "escalate_human"

    def test_disk_full_log_escalate(self):
        assert lookup_db_errp_override("disk_full") == "log_escalate"

    def test_syntax_error_escalate(self):
        assert lookup_db_errp_override("syntax_error") == "escalate_human"

    def test_connection_timeout_backoff(self):
        assert lookup_db_errp_override("connection_timeout") == "backoff_retry"

    def test_unknown_code_returns_none(self):
        assert lookup_db_errp_override("nonexistent_error") is None

    def test_key_m15_correction_deadlock_is_retryable(self):
        """Deadlock (mutation) SHOULD be retried — DB rolled back. Same principle as M15."""
        assert lookup_db_errp_override("deadlock_detected") == "backoff_retry"

    def test_key_m15_correction_unique_violation_not_retryable(self):
        """Unique violation (mutation) must NOT be retried with same input."""
        assert lookup_db_errp_override("unique_violation") == "escalate_human"


# --- Intent classification ---


class TestDbIntentClassification:
    def test_select_is_query(self):
        assert classify_db_intent("SELECT") == "query"

    def test_insert_is_mutation(self):
        assert classify_db_intent("INSERT") == "mutation"

    def test_update_is_mutation(self):
        assert classify_db_intent("UPDATE") == "mutation"

    def test_delete_is_mutation(self):
        assert classify_db_intent("DELETE") == "mutation"

    def test_create_table_is_mutation(self):
        assert classify_db_intent("CREATE TABLE") == "mutation"

    def test_drop_table_is_mutation(self):
        assert classify_db_intent("DROP TABLE") == "mutation"

    def test_explain_is_query(self):
        assert classify_db_intent("EXPLAIN") == "query"

    def test_pragma_is_query(self):
        assert classify_db_intent("PRAGMA") == "query"

    def test_vacuum_is_config_change(self):
        assert classify_db_intent("VACUUM") == "config_change"

    def test_none_defaults_to_query(self):
        assert classify_db_intent(None) == "query"

    def test_case_insensitive(self):
        assert classify_db_intent("select") == "query"
        assert classify_db_intent("Insert") == "mutation"


# --- Risk classification ---


class TestDbRiskClassification:
    def test_select_low(self):
        assert classify_db_risk("SELECT") == "low"

    def test_insert_low(self):
        assert classify_db_risk("INSERT") == "low"

    def test_update_medium(self):
        assert classify_db_risk("UPDATE") == "medium"

    def test_delete_high(self):
        assert classify_db_risk("DELETE") == "high"

    def test_drop_table_critical(self):
        assert classify_db_risk("DROP TABLE") == "critical"

    def test_truncate_critical(self):
        assert classify_db_risk("TRUNCATE") == "critical"

    def test_alter_table_high(self):
        assert classify_db_risk("ALTER TABLE") == "high"

    def test_create_index_low(self):
        assert classify_db_risk("CREATE INDEX") == "low"

    def test_none_defaults_low(self):
        assert classify_db_risk(None) == "low"


# --- Query pattern inference ---


class TestQueryPatternInference:
    def test_group_by_aggregation(self):
        assert infer_query_pattern("SELECT count(*) FROM t GROUP BY status") == "aggregation"

    def test_join_aggregate(self):
        assert infer_query_pattern("SELECT a.* FROM t JOIN a ON t.id = a.id GROUP BY a.type") == "join_aggregate"

    def test_join_filter(self):
        assert infer_query_pattern("SELECT * FROM t JOIN a ON t.id = a.id WHERE a.active = ?") == "join_filter"

    def test_point_lookup(self):
        assert infer_query_pattern("SELECT * FROM t WHERE id = ?") == "point_lookup"

    def test_filtered_scan(self):
        assert infer_query_pattern("SELECT * FROM t WHERE created_at > '2026-01-01'") == "filtered_scan"

    def test_full_scan(self):
        assert infer_query_pattern("SELECT * FROM t") == "full_scan"

    def test_none_returns_none(self):
        assert infer_query_pattern(None) is None

    def test_non_select_returns_none(self):
        assert infer_query_pattern("INSERT INTO t VALUES (1)") is None


# --- Structural anomaly detection ---


class TestDbStructuralAnomaly:
    def test_readonly_constraint_violation_is_anomaly(self):
        assert is_db_structural_anomaly(True, "unique_violation") is True

    def test_readonly_foreign_key_is_anomaly(self):
        assert is_db_structural_anomaly(True, "foreign_key_violation") is True

    def test_writable_constraint_not_anomaly(self):
        assert is_db_structural_anomaly(False, "unique_violation") is False

    def test_readonly_connection_error_not_anomaly(self):
        assert is_db_structural_anomaly(True, "connection_timeout") is False

    def test_none_readonly_not_anomaly(self):
        assert is_db_structural_anomaly(None, "unique_violation") is False


# --- DbExt query_pattern field ---


class TestDbExtQueryPattern:
    def test_query_pattern_roundtrip(self):
        ext = DbExt(
            db_system="postgresql",
            db_operation="SELECT",
            query_pattern="aggregation",
        )
        d = ext.to_dict()
        assert d["query_pattern"] == "aggregation"
        restored = DbExt.from_dict(d)
        assert restored.query_pattern == "aggregation"

    def test_query_pattern_omitted_when_none(self):
        ext = DbExt(db_system="postgresql")
        d = ext.to_dict()
        assert "query_pattern" not in d

    def test_risk_level_roundtrip(self):
        ext = DbExt(db_operation="DROP TABLE", risk_level="critical")
        d = ext.to_dict()
        assert d["risk_level"] == "critical"
        restored = DbExt.from_dict(d)
        assert restored.risk_level == "critical"


# --- Operation sets ---


class TestOperationSets:
    def test_ddl_operations_exist(self):
        assert "CREATE TABLE" in DDL_OPERATIONS
        assert "DROP TABLE" in DDL_OPERATIONS

    def test_dml_operations_exist(self):
        assert "INSERT" in DML_OPERATIONS
        assert "UPDATE" in DML_OPERATIONS
        assert "DELETE" in DML_OPERATIONS

    def test_dql_operations_exist(self):
        assert "SELECT" in DQL_OPERATIONS
        assert "EXPLAIN" in DQL_OPERATIONS

    def test_meta_operations_exist(self):
        assert "PRAGMA" in META_OPERATIONS
        assert "ANALYZE" in META_OPERATIONS
