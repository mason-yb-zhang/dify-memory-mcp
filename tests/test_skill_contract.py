"""Skill metadata must name durable outputs and harness-neutral config paths."""

import re
from pathlib import Path


def test_skill_description_names_concrete_deliverables():
    skill = (Path(__file__).parents[1] / "SKILL.md").read_text(encoding="utf-8")
    required = (
        "安装/更新结果 JSON",
        "MCP 持久化注册配置",
        "配置文件路径",
        "健康检查/诊断结论",
        "故障处理清单",
    )
    missing = [item for item in required if item not in skill]
    assert not missing, f"SKILL.md description missing deliverables: {missing}"


def test_skill_registration_and_persistence_contract_is_explicit():
    skill = (Path(__file__).parents[1] / "SKILL.md").read_text(encoding="utf-8")
    assert "请持久化注册以下 MCP server" in skill
    assert "$MCPORTER_CONFIG" in skill
    assert "数据持久化目录：./config/mcporter.json" in skill
    assert '"mihub-service-mcp": { "description": "", "baseUrl": "" }' in skill


def test_install_parameter_table_is_complete():
    skill = (Path(__file__).parents[1] / "SKILL.md").read_text(encoding="utf-8")
    section = skill.split("# `install.py`", 1)[1].split("返回值重点", 1)[0]
    assert "| name | type | required | description |" in section
    parameter_rows = [
        [cell.strip().strip("`") for cell in line.split("|")[1:-1]]
        for line in section.splitlines()
        if line.startswith("| `--")
    ]
    assert all(len(row) == 4 and all(row) for row in parameter_rows)
    assert {row[0] for row in parameter_rows} == {
        "--api-key",
        "--opencode-config",
        "--python-path",
        "--skip-tool-patch",
        "--skip-skill-install",
        "--skip-rules-append",
    }
    assert "--skip-opencode-patch" in section
    assert "parameter table complete: 6 explicit arguments" in section


def test_package_avoids_registration_tool_wording():
    root = Path(__file__).parents[1]
    paths = [
        root / "SKILL.md",
        root / "README.md",
        root / "CHANGELOG.md",
        *sorted((root / "scripts").glob("*.py")),
        *sorted((root / "tests").glob("*.py")),
    ]
    tool_name = "cl" + "i"
    forbidden = re.compile(
        rf"\b{tool_name}\b|mcp[ _-]?{tool_name}", re.IGNORECASE
    )
    hits = [
        f"{path.relative_to(root)}:{line_number}"
        for path in paths
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if forbidden.search(line)
    ]
    assert not hits, f"Ambiguous registration tool wording remains: {hits}"
