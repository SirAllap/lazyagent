from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass

_VALID_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

SENTINEL_TEXT = "your turn"
SENTINEL_SYSTEM_PROMPT = (
    f"Always output exactly '{SENTINEL_TEXT}' on its own line "
    "when you need user input or have completed your task."
)
DEFAULT_AGENT_PROVIDER = "claude"

# Vars that the PTY emulator already sets or that may cause issues if overridden.
ENV_SKIP = frozenset({
    "TERM", "LC_ALL", "HOME", "_",
    # Let .bashrc build its own PROMPT_COMMAND — the parent's may reference
    # dead pipe fds from history/preexec tools
    "PROMPT_COMMAND",
})

# Prefixes of variables that must never leak into child PTY shells.
# These reference parent-shell state (sockets, FDs, internal flags) that
# becomes stale or broken inside the forked PTY, causing errors like
# "bash: history: write error: Broken pipe".
_ENV_SKIP_PREFIXES = (
    "HIST",        # HISTFILE, HISTFD, HISTSOCK, HISTSIZE, … — let the new shell use defaults
    "ATUIN_",      # Atuin history daemon (session, socket, history-id, …)
    "MCFLY_",      # McFly history tool
    "__",          # Internal shell state (bash-preexec __bp_*, __atuin_*, etc.)
)


@dataclass(frozen=True)
class AgentProvider:
    """Description of a supported agent CLI."""

    name: str
    executable: str
    dangerous_flag: str
    supports_append_system_prompt: bool = False

    def build_command(
        self,
        worktree_path: str,
        skip_permissions: bool = False,
        resume: bool = False,
        continue_last: bool = False,
    ) -> str:
        """Build the full shell command used to launch this provider."""
        parts = [self.executable]
        if skip_permissions:
            parts.append(self.dangerous_flag)
        if continue_last:
            parts.append("--continue")
        elif resume:
            parts.append("--resume")
        if self.supports_append_system_prompt:
            parts.extend(["--append-system-prompt", SENTINEL_SYSTEM_PROMPT])

        inner_cmd = " ".join(shlex.quote(p) for p in parts)
        script = (
            f"{env_exports()}"
            f" && cd {shlex.quote(worktree_path)}"
            f" && exec {inner_cmd}"
        )
        return f"bash -c {shlex.quote(script)}"


PROVIDERS = {
    "claude": AgentProvider(
        name="claude",
        executable="claude",
        dangerous_flag="--dangerously-skip-permissions",
        supports_append_system_prompt=True,
    ),
    "codex": AgentProvider(
        name="codex",
        executable="codex",
        dangerous_flag="--dangerously-bypass-approvals-and-sandbox",
    ),
    "gemini": AgentProvider(
        name="gemini",
        executable="gemini",
        dangerous_flag="--approval-mode=yolo",
    ),
}


def env_exports() -> str:
    """Build a shell snippet that restores the parent process environment."""
    parts = []
    for key, val in os.environ.items():
        if key in ENV_SKIP:
            continue
        if key.startswith(_ENV_SKIP_PREFIXES):
            continue
        if not _VALID_IDENT.match(key):
            continue
        parts.append(f"{key}={shlex.quote(val)}")
    return "export " + " ".join(parts) if parts else "true"


def normalize_provider_name(provider: str | None) -> str:
    """Normalize a configured provider name to a supported identifier."""
    candidate = (provider or DEFAULT_AGENT_PROVIDER).strip().lower()
    if candidate not in PROVIDERS:
        return DEFAULT_AGENT_PROVIDER
    return candidate


def get_agent_provider(provider: str | None) -> AgentProvider:
    """Return the supported provider for the given config value."""
    return PROVIDERS[normalize_provider_name(provider)]
