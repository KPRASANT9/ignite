"""CLI modality extension — command-line tool invocations.

Captures CLI-specific interaction details: the command, arguments,
exit codes, and environment context. Used when agents invoke
shell commands, SDK CLI tools, or infrastructure CLIs.

Privacy: command_line and args MUST be scrubbed of inline secrets.
Use scrub_cli_secrets() before setting command_line/args.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Patterns for inline secrets in CLI commands
_CLI_SECRET_PATTERNS = [
    # --password=xxx, --token=xxx, --secret=xxx, --api-key=xxx
    re.compile(r"(--(?:password|token|secret|api[_-]?key|access[_-]?key|credential)\s*[=]\s*)\S+", re.I),
    # -p password (short flag with value)
    re.compile(r"(-p\s+)\S+"),
    # ENV_VAR=value command (e.g., API_KEY=xxx terraform apply)
    re.compile(
        r"\b((?:API_KEY|SECRET_KEY|ACCESS_KEY|TOKEN|PASSWORD|AWS_SECRET_ACCESS_KEY|"
        r"PRIVATE_KEY|AUTH_TOKEN|DB_PASSWORD)\s*=\s*)\S+",
        re.I,
    ),
]


def scrub_cli_secrets(command_line: str) -> str:
    """Scrub inline secrets from a CLI command string.

    Masks values in --password=xxx, --token=xxx, -p secret,
    and ENV_VAR=xxx patterns. Returns scrubbed command.
    """
    result = command_line
    for pattern in _CLI_SECRET_PATTERNS:
        result = pattern.sub(r"\g<1>***", result)
    return result


@dataclass
class CliExt:
    """Typed CLI interaction extension."""
    command: str | None = None          # e.g., "plaid", "stripe", "aws"
    subcommand: str | None = None       # e.g., "transactions list"
    command_line: str | None = None     # Full command, scrubbed of secrets
    args: list[str] = field(default_factory=list)  # Sanitized arguments
    exit_code: int | None = None        # Process exit code
    stdout_summary: str | None = None   # Truncated stdout
    stderr_summary: str | None = None   # Truncated stderr
    working_dir: str | None = None      # Working directory
    shell: str | None = None            # e.g., "bash", "zsh", "powershell"
    duration_ms: int | None = None      # Command execution time

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "cli_ext"}
        if self.command is not None:
            d["command"] = self.command
        if self.subcommand is not None:
            d["subcommand"] = self.subcommand
        if self.command_line is not None:
            d["command_line"] = self.command_line
        if self.args:
            d["args"] = self.args
        if self.exit_code is not None:
            d["exit_code"] = self.exit_code
        if self.stdout_summary is not None:
            d["stdout_summary"] = self.stdout_summary
        if self.stderr_summary is not None:
            d["stderr_summary"] = self.stderr_summary
        if self.working_dir is not None:
            d["working_dir"] = self.working_dir
        if self.shell is not None:
            d["shell"] = self.shell
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CliExt:
        return cls(
            command=data.get("command"),
            subcommand=data.get("subcommand"),
            command_line=data.get("command_line"),
            args=data.get("args", []),
            exit_code=data.get("exit_code"),
            stdout_summary=data.get("stdout_summary"),
            stderr_summary=data.get("stderr_summary"),
            working_dir=data.get("working_dir"),
            shell=data.get("shell"),
            duration_ms=data.get("duration_ms"),
        )
