"""Smoke test: proves the package installs and imports from src/."""

import sys
from pathlib import Path

import setuhaul


def test_python_version_is_pinned():
    assert sys.version_info[:2] >= (3, 13)


def test_package_imports_from_src():
    package_dir = Path(setuhaul.__file__).parent
    assert package_dir.parent.name == "src"
