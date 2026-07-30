"""Database modality extension — aligned with OTel DB semantic conventions.

Second-priority modality after API. OTel DB semconv provides a well-tested
attribute set. This extension captures DB-specific interaction details
that enrich API traces (e.g., Plaid's underlying PostgreSQL queries).

Reference: https://opentelemetry.io/docs/specs/semconv/database/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DbExt:
    """Typed database interaction extension (OTel DB semconv aligned).

    Captures database operations with enough detail for L2 pattern analysis:
    query timing, row counts, connection pooling, and transaction state.
    """
    db_system: str | None = None        # e.g., "postgresql", "mysql", "redis", "mongodb"
    db_name: str | None = None          # Database/schema name
    db_operation: str | None = None     # e.g., "SELECT", "INSERT", "UPDATE", "DELETE"
    db_statement: str | None = None     # Sanitized SQL/query (no values, just structure)
    db_table: str | None = None         # Primary table/collection
    rows_affected: int | None = None    # Number of rows returned/modified
    query_fingerprint: str | None = None  # Normalized query structure for dedup (e.g., "SELECT * FROM t WHERE id = ?")
    plan_cost: float | None = None      # Query planner estimated cost (pg: total_cost, mysql: cost)
    connection_pool_name: str | None = None  # Pool identifier
    connection_id: str | None = None    # Specific connection within pool
    transaction_id: str | None = None   # Transaction context (for multi-statement txns)
    is_readonly: bool | None = None     # True for reads, False for writes
    risk_level: str | None = None      # "low", "medium", "high", "critical" — DDL risk weighting for Planner
    query_pattern: str | None = None   # Analytical shape: "point_lookup", "filtered_scan", "aggregation", "join_filter", etc.

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "db_ext"}
        if self.db_system is not None:
            d["db_system"] = self.db_system
        if self.db_name is not None:
            d["db_name"] = self.db_name
        if self.db_operation is not None:
            d["db_operation"] = self.db_operation
        if self.db_statement is not None:
            d["db_statement"] = self.db_statement
        if self.db_table is not None:
            d["db_table"] = self.db_table
        if self.rows_affected is not None:
            d["rows_affected"] = self.rows_affected
        if self.query_fingerprint is not None:
            d["query_fingerprint"] = self.query_fingerprint
        if self.plan_cost is not None:
            d["plan_cost"] = self.plan_cost
        if self.connection_pool_name is not None:
            d["connection_pool_name"] = self.connection_pool_name
        if self.connection_id is not None:
            d["connection_id"] = self.connection_id
        if self.transaction_id is not None:
            d["transaction_id"] = self.transaction_id
        if self.is_readonly is not None:
            d["is_readonly"] = self.is_readonly
        if self.risk_level is not None:
            d["risk_level"] = self.risk_level
        if self.query_pattern is not None:
            d["query_pattern"] = self.query_pattern
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DbExt:
        return cls(
            db_system=data.get("db_system"),
            db_name=data.get("db_name"),
            db_operation=data.get("db_operation"),
            db_statement=data.get("db_statement"),
            db_table=data.get("db_table"),
            rows_affected=data.get("rows_affected"),
            query_fingerprint=data.get("query_fingerprint"),
            plan_cost=data.get("plan_cost"),
            connection_pool_name=data.get("connection_pool_name"),
            connection_id=data.get("connection_id"),
            transaction_id=data.get("transaction_id"),
            is_readonly=data.get("is_readonly"),
            risk_level=data.get("risk_level"),
            query_pattern=data.get("query_pattern"),
        )
