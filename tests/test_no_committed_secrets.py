"""Tracked repository files must not contain live credential formats."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


PATTERNS = {
    "gateway key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "mify key": re.compile(r"\bdataset-[A-Za-z0-9_-]{20,}"),
    "private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "plaintext secret": re.compile(
        r"(?i)(?:app_secret|client_secret|api_secret|password)\s*[=:]\s*"
        r"['\"](?P<value>[^'\"]+)['\"]"
    ),
    "node token": re.compile(
        r"(?i)(?:node_token|parent_node_token|root_node_token)\s*[:=]\s*"
        r"['\"]?(?P<value>(?!<)(?!(?-i:[A-Z_]+\b))(?![A-Za-z_]+\.[A-Za-z_])"
        r"[A-Za-z0-9_-]{10,})"
    ),
}
PLACEHOLDERS = (
    "your",
    "xxxx",
    "example",
    "test",
    "invalid",
    "replace",
    "placeholder",
    "fill",
    "在此",
    "填入",
)


def _repo_root() -> Path:
    """Locate the repository root instead of assuming a fixed nesting depth.

    The old ``Path(__file__).parents[2]`` broke as soon as the package was
    checked out at a different depth. Asking git is always correct.
    """
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if probe.returncode == 0 and probe.stdout.strip():
        return Path(probe.stdout.strip())
    return Path(__file__).resolve().parents[1]


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [root / line for line in result.stdout.splitlines() if line]


def is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDERS)


def test_no_live_credentials_are_tracked():
    root = _repo_root()
    findings = []
    for path in tracked_files(root):
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(content):
                value = match.groupdict().get("value") or match.group(0)
                if not is_placeholder(value):
                    findings.append(f"{path.relative_to(root)}: {label}")
    assert not findings, f"Live credentials found in tracked files: {findings}"
