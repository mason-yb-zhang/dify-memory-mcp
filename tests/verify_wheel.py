"""Verify release wheel contents."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_wheel.py <wheel>", file=sys.stderr)
        return 2

    wheel = Path(sys.argv[1])
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    required = {
        "runtime_config.py",
        "share/mify-memory-mcp/config/mcporter.json",
        "share/mify-memory-mcp/docs/README.md",
        "share/mify-memory-mcp/docs/SKILL.md",
        "share/mify-memory-mcp/docs/references/installation.md",
        "share/mify-memory-mcp/docs/references/mcp-registration.md",
        "share/mify-memory-mcp/docs/references/api-reference.md",
        "share/mify-memory-mcp/docs/references/architecture.md",
        "share/mify-memory-mcp/docs/references/environment.md",
        "share/mify-memory-mcp/docs/references/troubleshooting.md",
    }
    missing = sorted(
        name
        for name in required
        if not any(entry == name or entry.endswith(f"/{name}") for entry in names)
    )
    if missing:
        print(f"missing wheel entries: {', '.join(missing)}", file=sys.stderr)
        return 1

    print(f"wheel contents verified: {wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
