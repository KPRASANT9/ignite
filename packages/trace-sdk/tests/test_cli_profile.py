"""Tests for CLI modality profile — error taxonomy, intent/risk classification, secret scrubbing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ignite_trace.profiles.cli import (
    classify_cli_intent,
    classify_cli_risk,
    exit_code_to_error,
    lookup_cli_errp_override,
    CLI_ERROR_CODES,
    CLI_ERROR_CODE_CORRECTIONS,
)
from ignite_trace.extensions.cli_ext import CliExt, scrub_cli_secrets
from ignite_trace.sanitizer import sanitize_cli_command


# --- Error taxonomy ---


class TestCliErrorTaxonomy:
    def test_all_error_codes_have_corrections(self):
        for code in CLI_ERROR_CODES:
            assert code in CLI_ERROR_CODE_CORRECTIONS, f"Missing correction for {code}"

    def test_command_not_found_never_retry(self):
        assert CLI_ERROR_CODES["command_not_found"]["retry"] == "never"
        assert lookup_cli_errp_override("command_not_found") == "escalate_human"

    def test_oom_killed_unsafe(self):
        assert CLI_ERROR_CODES["oom_killed"]["retry"] == "unsafe"
        assert lookup_cli_errp_override("oom_killed") == "log_escalate"

    def test_timeout_conditional(self):
        assert CLI_ERROR_CODES["timeout"]["retry"] == "conditional"
        assert lookup_cli_errp_override("timeout") == "backoff_retry"

    def test_test_failure_never_retry(self):
        assert lookup_cli_errp_override("test_failure") == "escalate_human"

    def test_unknown_code_returns_none(self):
        assert lookup_cli_errp_override("unknown") is None


# --- Exit code mapping ---


class TestExitCodeMapping:
    def test_success(self):
        assert exit_code_to_error(0) is None

    def test_general_error(self):
        assert exit_code_to_error(1) == "general_error"

    def test_command_not_found(self):
        assert exit_code_to_error(127) == "command_not_found"

    def test_oom_kill(self):
        assert exit_code_to_error(137) == "oom_killed"

    def test_timeout(self):
        assert exit_code_to_error(124) == "timeout"

    def test_permission_denied(self):
        assert exit_code_to_error(126) == "permission_denied"

    def test_none(self):
        assert exit_code_to_error(None) is None

    def test_unknown(self):
        assert exit_code_to_error(42) is None


# --- Intent classification ---


class TestCliIntentClassification:
    def test_ls_is_query(self):
        assert classify_cli_intent("ls") == "query"

    def test_grep_is_query(self):
        assert classify_cli_intent("grep") == "query"

    def test_rm_is_mutation(self):
        assert classify_cli_intent("rm") == "mutation"

    def test_git_push_is_mutation(self):
        assert classify_cli_intent("git", "push") == "mutation"

    def test_git_status_is_query(self):
        assert classify_cli_intent("git", "status") == "query"

    def test_make_is_state_transition(self):
        assert classify_cli_intent("make") == "state_transition"

    def test_docker_build_is_state_transition(self):
        assert classify_cli_intent("docker", "build") == "state_transition"

    def test_kubectl_delete_is_mutation(self):
        assert classify_cli_intent("kubectl", "delete") == "mutation"

    def test_default_is_query(self):
        assert classify_cli_intent("some_unknown_tool") == "query"

    def test_none_is_query(self):
        assert classify_cli_intent(None) == "query"


# --- Risk classification ---


class TestCliRiskClassification:
    def test_ls_low(self):
        assert classify_cli_risk("ls") == "low"

    def test_git_push_high(self):
        assert classify_cli_risk("git", "push") == "high"

    def test_git_commit_medium(self):
        assert classify_cli_risk("git", "commit") == "medium"

    def test_rm_rf_critical(self):
        assert classify_cli_risk("rm", command_line="rm -rf /tmp/test") == "critical"

    def test_terraform_destroy_critical(self):
        assert classify_cli_risk("terraform", "destroy") == "critical"

    def test_default_low(self):
        assert classify_cli_risk("echo") == "low"

    def test_none_low(self):
        assert classify_cli_risk(None) == "low"


# --- Secret scrubbing ---


class TestCliSecretScrubbing:
    def test_scrub_password_flag(self):
        result = scrub_cli_secrets("mysql -u root --password=s3cret123 mydb")
        assert "s3cret123" not in result
        assert "--password=***" in result

    def test_scrub_token_flag(self):
        result = scrub_cli_secrets("curl -H 'Auth' --token=abc123xyz myurl")
        assert "abc123xyz" not in result
        assert "--token=***" in result

    def test_scrub_env_var(self):
        result = scrub_cli_secrets("API_KEY=supersecret terraform apply")
        assert "supersecret" not in result
        assert "API_KEY=***" in result

    def test_scrub_aws_secret(self):
        result = scrub_cli_secrets("AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE aws s3 ls")
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_no_scrub_normal_command(self):
        cmd = "git push -u origin main"
        assert scrub_cli_secrets(cmd) == cmd

    def test_scrub_short_password_flag(self):
        result = scrub_cli_secrets("mysql -u root -p mysecret")
        assert "mysecret" not in result


# --- Sanitizer integration ---


class TestSanitizerCliCommand:
    def test_sanitize_cli_command(self):
        result = sanitize_cli_command("mysql --password=s3cret123 mydb")
        assert "s3cret123" not in result

    def test_sanitize_preserves_normal(self):
        result = sanitize_cli_command("git status")
        assert result == "git status"


# --- CliExt roundtrip with new fields ---


class TestCliExtEnhanced:
    def test_command_line_roundtrip(self):
        original = CliExt(
            command="git",
            subcommand="push",
            command_line="git push -u origin main",
            exit_code=0,
            working_dir="/home/user/project",
            shell="bash",
            duration_ms=15000,
        )
        d = original.to_dict()
        restored = CliExt.from_dict(d)
        assert restored.command_line == "git push -u origin main"
        assert restored.duration_ms == 15000

    def test_scrubbed_command_in_trace(self):
        """Demonstrate privacy workflow: scrub before constructing CliExt."""
        raw_cmd = "mysql --password=s3cret123 mydb"
        scrubbed = scrub_cli_secrets(raw_cmd)
        ext = CliExt(command="mysql", command_line=scrubbed, exit_code=0)
        d = ext.to_dict()
        assert "s3cret123" not in d["command_line"]
        assert "--password=***" in d["command_line"]
