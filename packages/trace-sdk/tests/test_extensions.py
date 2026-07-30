"""Tests for IGNITE modality extensions — Batch 3.

Covers:
- Extension dataclasses (ApiExt, DbExt, CliExt, MsgExt, WebExt)
- to_dict() / from_dict() serialization roundtrips
- SpanBuilder.modality_ext() integration
- ManifestTarget modality field
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_trace.extensions import ApiExt, DbExt, CliExt, MsgExt, WebExt
from ignite_trace.span import SpanBuilder
from ignite_trace.manifest import ManifestTarget, ExplorationManifest


# --- ApiExt ---


class TestApiExt:
    def test_defaults(self):
        ext = ApiExt()
        assert ext.method is None
        assert ext.url is None

    def test_to_dict(self):
        ext = ApiExt(method="POST", url="https://api.plaid.com/transactions/get", status_code=200)
        d = ext.to_dict()
        assert d["type"] == "api_ext"
        assert d["method"] == "POST"
        assert d["status_code"] == 200

    def test_to_dict_omits_none(self):
        ext = ApiExt(method="GET")
        d = ext.to_dict()
        assert "url" not in d
        assert "status_code" not in d

    def test_roundtrip(self):
        original = ApiExt(method="POST", url="https://example.com", status_code=201, idempotency_key="ik-123")
        d = original.to_dict()
        restored = ApiExt.from_dict(d)
        assert restored.method == "POST"
        assert restored.url == "https://example.com"
        assert restored.status_code == 201
        assert restored.idempotency_key == "ik-123"


# --- DbExt ---


class TestDbExt:
    def test_defaults(self):
        ext = DbExt()
        assert ext.db_system is None

    def test_to_dict(self):
        ext = DbExt(db_system="postgresql", db_operation="SELECT", db_table="transactions", rows_affected=42)
        d = ext.to_dict()
        assert d["type"] == "db_ext"
        assert d["db_system"] == "postgresql"
        assert d["rows_affected"] == 42

    def test_roundtrip(self):
        original = DbExt(
            db_system="postgresql", db_name="plaid_db", db_operation="INSERT",
            db_table="items", rows_affected=1, is_readonly=False,
            transaction_id="txn-abc",
        )
        restored = DbExt.from_dict(original.to_dict())
        assert restored.db_system == "postgresql"
        assert restored.db_table == "items"
        assert restored.is_readonly is False
        assert restored.transaction_id == "txn-abc"


# --- CliExt ---


class TestCliExt:
    def test_to_dict(self):
        ext = CliExt(command="plaid", subcommand="transactions list", exit_code=0)
        d = ext.to_dict()
        assert d["type"] == "cli_ext"
        assert d["command"] == "plaid"
        assert d["exit_code"] == 0

    def test_roundtrip(self):
        original = CliExt(
            command="aws", subcommand="s3 ls", args=["--bucket", "data"],
            exit_code=0, shell="bash",
        )
        restored = CliExt.from_dict(original.to_dict())
        assert restored.command == "aws"
        assert restored.args == ["--bucket", "data"]
        assert restored.shell == "bash"

    def test_empty_args_omitted(self):
        ext = CliExt(command="ls")
        d = ext.to_dict()
        assert "args" not in d


# --- MsgExt ---


class TestMsgExt:
    def test_to_dict(self):
        ext = MsgExt(system="nats", operation="publish", topic="traces.new")
        d = ext.to_dict()
        assert d["type"] == "msg_ext"
        assert d["system"] == "nats"
        assert d["topic"] == "traces.new"

    def test_roundtrip(self):
        original = MsgExt(
            system="kafka", operation="subscribe", topic="events",
            delivery="at_least_once", consumer_group="pipeline",
        )
        restored = MsgExt.from_dict(original.to_dict())
        assert restored.system == "kafka"
        assert restored.delivery == "at_least_once"
        assert restored.consumer_group == "pipeline"

    def test_webhook_fields_to_dict(self):
        ext = MsgExt(
            system="webhook", operation="receive",
            event_type="ITEM", webhook_code="ERROR",
            delivery="at_least_once", ordering="unordered",
            replay_support=False, retry_window_hours=24,
        )
        d = ext.to_dict()
        assert d["type"] == "msg_ext"
        assert d["event_type"] == "ITEM"
        assert d["webhook_code"] == "ERROR"
        assert d["ordering"] == "unordered"
        assert d["replay_support"] is False
        assert d["retry_window_hours"] == 24

    def test_webhook_fields_omitted_when_none(self):
        ext = MsgExt(system="nats", operation="publish")
        d = ext.to_dict()
        assert "event_type" not in d
        assert "webhook_code" not in d
        assert "ordering" not in d
        assert "replay_support" not in d
        assert "retry_window_hours" not in d

    def test_webhook_roundtrip(self):
        original = MsgExt(
            system="webhook", operation="receive", topic="ITEM",
            event_type="ITEM", webhook_code="LOGIN_REPAIRED",
            delivery="at_least_once", ordering="unordered",
            replay_support=False, retry_window_hours=24,
            content_type="application/json",
        )
        restored = MsgExt.from_dict(original.to_dict())
        assert restored.event_type == "ITEM"
        assert restored.webhook_code == "LOGIN_REPAIRED"
        assert restored.ordering == "unordered"
        assert restored.replay_support is False
        assert restored.retry_window_hours == 24


# --- WebExt ---


class TestWebExt:
    def test_to_dict(self):
        ext = WebExt(page_url="https://dashboard.plaid.com", action="click", selector="#login-btn")
        d = ext.to_dict()
        assert d["type"] == "web_ext"
        assert d["page_url"] == "https://dashboard.plaid.com"

    def test_roundtrip(self):
        original = WebExt(
            page_url="https://example.com", page_title="Home",
            action="navigate", viewport_width=1920, viewport_height=1080,
        )
        restored = WebExt.from_dict(original.to_dict())
        assert restored.page_title == "Home"
        assert restored.viewport_width == 1920


# --- SpanBuilder modality_ext ---


class TestSpanBuilderModalityExt:
    def test_no_ext_by_default(self):
        sb = SpanBuilder("api_call", "test", 1, "t1")
        d = sb.to_dict()
        assert "modality_ext" not in d

    def test_set_api_ext(self):
        sb = SpanBuilder("api_call", "POST /transactions/get", 1, "t1")
        sb.modality_ext(ApiExt(method="POST", status_code=200))
        d = sb.to_dict()
        assert d["modality_ext"]["type"] == "api_ext"
        assert d["modality_ext"]["method"] == "POST"

    def test_set_db_ext(self):
        sb = SpanBuilder("api_call", "SELECT transactions", 1, "t1")
        sb.classify(modality="db")
        sb.modality_ext(DbExt(db_system="postgresql", db_operation="SELECT", rows_affected=10))
        d = sb.to_dict()
        assert d["modality"] == "db"
        assert d["modality_ext"]["type"] == "db_ext"
        assert d["modality_ext"]["db_system"] == "postgresql"

    def test_set_cli_ext(self):
        sb = SpanBuilder("api_call", "plaid transactions list", 1, "t1")
        sb.classify(modality="cli")
        sb.modality_ext(CliExt(command="plaid", exit_code=0))
        d = sb.to_dict()
        assert d["modality_ext"]["type"] == "cli_ext"

    def test_set_raw_dict(self):
        sb = SpanBuilder("api_call", "test", 1, "t1")
        sb.modality_ext({"type": "custom_ext", "foo": "bar"})
        d = sb.to_dict()
        assert d["modality_ext"]["type"] == "custom_ext"

    def test_full_chain(self):
        sb = SpanBuilder("api_call", "POST /transfer/create", 1, "t1")
        sb.classify(modality="api", request_intent="mutation", response_outcome="success")
        sb.modality_ext(ApiExt(method="POST", path="/transfer/create", status_code=200, idempotency_key="ik-42"))
        sb.expect("transfer created successfully", confidence=0.9)
        sb.actual("transfer created successfully")
        sb.divergence(0.0)
        d = sb.to_dict()
        assert d["modality"] == "api"
        assert d["modality_ext"]["idempotency_key"] == "ik-42"
        assert d["expected_outcome"]["confidence"] == 0.9
        assert d["divergence_score"] == 0.0


# --- ManifestTarget modality ---


class TestManifestTargetModality:
    def test_default_modality_is_api(self):
        target = ManifestTarget(id="t1", domain="auth", span_kind="auth_flow", priority="P1", tier=1)
        assert target.modality == "api"

    def test_custom_modality(self):
        target = ManifestTarget(
            id="t2", domain="db", span_kind="api_call", priority="P2", tier=2,
            modality="db",
        )
        assert target.modality == "db"

    def test_from_dict_parses_modality(self):
        manifest = ExplorationManifest.from_dict({
            "system": "plaid",
            "targets": [
                {"id": "t1", "domain": "auth", "span_kind": "auth_flow", "modality": "api"},
                {"id": "t2", "domain": "db", "span_kind": "api_call", "modality": "db"},
                {"id": "t3", "domain": "cli", "span_kind": "api_call"},  # default
            ],
        })
        assert manifest.targets[0].modality == "api"
        assert manifest.targets[1].modality == "db"
        assert manifest.targets[2].modality == "api"  # default
