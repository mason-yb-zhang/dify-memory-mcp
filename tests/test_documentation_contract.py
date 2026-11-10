"""Cross-file documentation must match the current runtime contract."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
TOOLS = {
    "add_memory",
    "search_memory",
    "update_memory",
    "delete_memory",
    "list_memories",
    "decay_memories",
    "promote_memory",
    "get_memory",
    "flush_offline_queue",
    "get_health",
    "save_session",
    "list_sessions",
    "search_session",
    "get_session_detail",
    "delete_session",
}


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_versions_match_across_release_files():
    pyproject = read("pyproject.toml")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE).group(1)
    assert re.search(rf"^version: {re.escape(version)}$", read("SKILL.md"), re.MULTILINE)
    assert f'SERVICE_VERSION = "{version}"' in read("scripts/server.py")
    assert f'"version": "{version}"' in read("scripts/memory_service.py")
    assert f"## {version} - " in read("CHANGELOG.md")


def test_readme_and_skill_document_all_tools():
    for path in ("README.md", "SKILL.md"):
        document = read(path)
        missing = sorted(tool for tool in TOOLS if f"`{tool}`" not in document)
        assert not missing, f"{path} missing tool docs: {missing}"


def test_file_inventory_covers_current_package():
    document = read("README.md")
    for required in (
        "mcporter.json",
        "runtime_config.py",
        "memory_service.py",
        "openclaw_session.py",
        "flush_offline_queue.py",
        "tests/",
    ):
        assert required in document, f"README.md file inventory missing {required}"


def test_current_docs_describe_local_resilience_and_harness_restart():
    full_readme = read("README.md")
    marker = "## 发布历史" if "## 发布历史" in full_readme else "## 更新日志"
    readme = full_readme.split(marker, 1)[0]
    skill = read("SKILL.md")
    assert "本地缓存和离线队列" in readme
    assert "本地韧性" in skill
    assert "**重启 OpenCode** 即可生效" not in readme
    assert "不存本地" not in readme


def test_opencode_setup_uses_canonical_persistent_key():
    setup = read("scripts/setup.py")
    assert '"mify-memory": {' in setup
    assert '"memory": {' not in setup
    assert "mcp.mify-memory.environment" in read("README.md")
