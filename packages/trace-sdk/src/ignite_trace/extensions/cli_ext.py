"""CLI modality extension — command-line tool invocations.

Captures CLI-specific interaction details: the command, arguments,
exit codes, and environment context. Used when agents invoke
shell commands, SDK CLI tools, or infrastructure CLIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CliExt:
    """Typed CLI interaction extension."""
    command: str | None = None          # e.g., "plaid", "stripe", "aws"
    subcommand: str | None = None       # e.g., "transactions list"
    args: list[str] = field(default_factory=list)  # Sanitized arguments
    exit_code: int | None = None        # Process exit code
    stdout_summary: str | None = None   # Truncated stdout
    stderr_summary: str | None = None   # Truncated stderr
    working_dir: str | None = None      # Working directory
    shell: str | None = None            # e.g., "bash", "zsh", "powershell"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "cli_ext"}
        if self.command is not None:
            d["command"] = self.command
        if self.subcommand is not None:
            d["subcommand"] = self.subcommand
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
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CliExt:
        return cls(
            command=data.get("command"),
            subcommand=data.get("subcommand"),
            args=data.get("args", []),
            exit_code=data.get("exit_code"),
            stdout_summary=data.get("stdout_summary"),
            stderr_summary=data.get("stderr_summary"),
            working_dir=data.get("working_dir"),
            shell=data.get("shell"),
        )
