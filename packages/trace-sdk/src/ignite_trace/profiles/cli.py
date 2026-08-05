"""CLI modality profile — error taxonomy, intent classification, and ErrP overrides.

Simplest modality: commands are self-describing, exit codes are standardized.
Same template as DB/Web profiles: error codes → retry strategies,
command name → intent classification, command → risk weighting.

Source: Honey CLI Modality Product Spec.
"""

from __future__ import annotations


# --- CLI error taxonomy ---
# 5 families mapped by exit code + stderr patterns.
# Standard Unix exit codes: 0=success, 1=general error, 2=usage error,
# 124=timeout, 126=permission, 127=not found, 128+N=signal N (137=SIGKILL/OOM).

CLI_ERROR_CODES: dict[str, dict[str, str]] = {
    # General errors — conditional retry
    "general_error": {"family": "general", "retry": "conditional", "exit_code": "1"},
    "usage_error": {"family": "general", "retry": "never", "exit_code": "2"},
    # Command resolution — never retry
    "command_not_found": {"family": "resolution", "retry": "never", "exit_code": "127"},
    "permission_denied": {"family": "resolution", "retry": "never", "exit_code": "126"},
    # Timeout — conditional retry with longer timeout
    "timeout": {"family": "timeout", "retry": "conditional", "exit_code": "124"},
    # Signal/resource — unsafe to retry
    "oom_killed": {"family": "resource", "retry": "unsafe", "exit_code": "137"},
    "sigterm": {"family": "resource", "retry": "conditional", "exit_code": "143"},
    "sigint": {"family": "resource", "retry": "never", "exit_code": "130"},
    # Build/test failures — never retry same input
    "test_failure": {"family": "build", "retry": "never"},
    "build_failure": {"family": "build", "retry": "never"},
    "lint_failure": {"family": "build", "retry": "never"},
    # Network errors (from stderr patterns) — safe to retry
    "network_error": {"family": "network", "retry": "safe"},
    "dns_resolution_failed": {"family": "network", "retry": "conditional"},
}


# --- ErrP correction overrides ---

CLI_ERROR_CODE_CORRECTIONS: dict[str, str] = {
    # General
    "general_error": "backoff_retry",
    "usage_error": "escalate_human",
    # Resolution — never retry
    "command_not_found": "escalate_human",
    "permission_denied": "escalate_human",
    # Timeout — retry with longer timeout
    "timeout": "backoff_retry",
    # Resource
    "oom_killed": "log_escalate",
    "sigterm": "backoff_retry",
    "sigint": "escalate_human",
    # Build/test — fix input, don't retry
    "test_failure": "escalate_human",
    "build_failure": "escalate_human",
    "lint_failure": "escalate_human",
    # Network — retry
    "network_error": "backoff_retry",
    "dns_resolution_failed": "backoff_retry",
}


def lookup_cli_errp_override(error_code: str) -> str | None:
    """Look up CLI-specific ErrP correction override by error code.

    Returns CorrectionAction value string, or None if unknown error code.
    """
    return CLI_ERROR_CODE_CORRECTIONS.get(error_code)


# --- Exit code → error code mapping ---

_EXIT_CODE_MAP: dict[int, str] = {
    1: "general_error",
    2: "usage_error",
    124: "timeout",
    126: "permission_denied",
    127: "command_not_found",
    130: "sigint",
    137: "oom_killed",
    143: "sigterm",
}


def exit_code_to_error(exit_code: int | None) -> str | None:
    """Map a Unix exit code to an error code string.

    Returns None for exit code 0 (success) or unknown codes.
    """
    if exit_code is None or exit_code == 0:
        return None
    return _EXIT_CODE_MAP.get(exit_code)


# --- Intent classification ---

# Commands that read/inspect (query)
_QUERY_COMMANDS = frozenset({
    "ls", "cat", "head", "tail", "less", "more", "find", "grep", "rg",
    "wc", "du", "df", "ps", "top", "htop", "env", "echo", "pwd",
    "which", "whereis", "file", "stat", "dig", "nslookup", "curl",
    "wget", "git log", "git status", "git diff", "git show", "git branch",
    "docker ps", "docker images", "kubectl get", "kubectl describe",
    "terraform plan", "terraform show",
})

# Commands that modify state (mutation)
_MUTATION_COMMANDS = frozenset({
    "rm", "mv", "cp", "mkdir", "rmdir", "touch", "chmod", "chown",
    "ln", "sed", "awk", "tee", "truncate",
    "git add", "git commit", "git push", "git merge", "git rebase",
    "git reset", "git checkout", "git rm", "git mv",
    "docker rm", "docker rmi", "docker stop", "docker kill",
    "kubectl delete", "kubectl apply", "kubectl patch",
    "terraform apply", "terraform destroy",
    "pip install", "pip uninstall", "npm install", "npm uninstall",
    "apt install", "apt remove", "brew install", "brew uninstall",
})

# Commands that are state transitions (lifecycle events)
_STATE_TRANSITION_COMMANDS = frozenset({
    "make", "build", "deploy", "init",
    "git init", "git clone",
    "docker build", "docker run", "docker compose up",
    "kubectl create", "terraform init",
    "npm run", "yarn run", "cargo build", "cargo run",
})


def classify_cli_intent(
    command: str | None = None,
    subcommand: str | None = None,
) -> str:
    """Classify CLI command intent.

    Uses command + subcommand matching against known command sets.
    Returns RequestIntent value string.
    """
    if command is None:
        return "query"

    cmd_lower = command.lower().strip()

    # Try compound match first (e.g., "git push")
    if subcommand:
        compound = f"{cmd_lower} {subcommand.lower().strip()}"
        if compound in _STATE_TRANSITION_COMMANDS:
            return "state_transition"
        if compound in _MUTATION_COMMANDS:
            return "mutation"
        if compound in _QUERY_COMMANDS:
            return "query"

    # Single command match
    if cmd_lower in _STATE_TRANSITION_COMMANDS:
        return "state_transition"
    if cmd_lower in _MUTATION_COMMANDS:
        return "mutation"
    if cmd_lower in _QUERY_COMMANDS:
        return "query"

    return "query"  # safe default


# --- Risk classification ---

_CRITICAL_COMMANDS = frozenset({
    "rm -rf", "terraform destroy", "kubectl delete",
    "docker rmi", "docker system prune",
    "git push --force", "git reset --hard",
})

_HIGH_RISK_COMMANDS = frozenset({
    "git push", "terraform apply", "docker build",
    "kubectl apply", "deploy", "rm",
    "pip install", "npm install",
})

_MEDIUM_RISK_COMMANDS = frozenset({
    "git commit", "git merge", "git rebase",
    "make", "build", "mv", "cp",
    "chmod", "chown",
})


def classify_cli_risk(
    command: str | None = None,
    subcommand: str | None = None,
    command_line: str | None = None,
) -> str:
    """Classify CLI command risk level.

    Returns: "low", "medium", "high", or "critical".
    """
    if command is None:
        return "low"

    cmd_lower = command.lower().strip()

    # Check full command line for critical patterns
    if command_line:
        cl_lower = command_line.lower()
        for critical in _CRITICAL_COMMANDS:
            if critical in cl_lower:
                return "critical"

    # Compound match
    if subcommand:
        compound = f"{cmd_lower} {subcommand.lower().strip()}"
        if compound in _CRITICAL_COMMANDS:
            return "critical"
        if compound in _HIGH_RISK_COMMANDS:
            return "high"
        if compound in _MEDIUM_RISK_COMMANDS:
            return "medium"

    # Single match
    if cmd_lower in _CRITICAL_COMMANDS:
        return "critical"
    if cmd_lower in _HIGH_RISK_COMMANDS:
        return "high"
    if cmd_lower in _MEDIUM_RISK_COMMANDS:
        return "medium"

    return "low"
