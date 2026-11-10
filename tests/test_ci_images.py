"""GitLab CI must use internal DevX images reachable by shared runners."""

from __future__ import annotations

import re
from pathlib import Path


def test_ci_images_are_internal_devx_images():
    root = Path(__file__).parents[2]
    ci_config = (root / ".gitlab-ci.yml").read_text(encoding="utf-8")
    images = re.findall(r"^\s*image:\s*(.+?)\s*$", ci_config, re.MULTILINE)

    assert images, ".gitlab-ci.yml must declare job images"
    invalid = [
        image
        for image in images
        if not image.startswith("micr.cloud.mioffice.cn/devx-build-image/")
    ]
    assert not invalid, f"CI images must come from DevX registry: {invalid}"


def test_python_jobs_use_project_virtualenv():
    root = Path(__file__).parents[2]
    ci_config = (root / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert ci_config.count("apt-get install -y --no-install-recommends python3.12-venv") == 2
    assert ci_config.count("python -m venv .venv") == 2
    assert ".venv/bin/python -m pip install" in ci_config
    assert ".venv/bin/python -m pytest" in ci_config
    assert ".venv/bin/python -m build" in ci_config
