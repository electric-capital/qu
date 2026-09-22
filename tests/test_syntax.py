"""Syntax validation for all Python source files in the project.

Walks the project tree and py_compiles every .py file as a parametrized
pytest test, so syntax errors surface alongside the rest of the test suite.
"""

import os
import py_compile
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

EXCLUDED_DIRS = {
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "node_modules",
    "data",
    ".pytest_cache",
    "devplans",
}


def _collect_python_files() -> list[Path]:
    """Walk the project tree and return all .py files, excluding non-source dirs."""
    files = []
    for dirpath, dirs, filenames in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        dirs.sort()
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                files.append(Path(dirpath) / filename)
    return files


_python_files = _collect_python_files()


@pytest.mark.parametrize(
    "filepath",
    _python_files,
    ids=[str(f.relative_to(PROJECT_ROOT)) for f in _python_files],
)
def test_syntax(filepath: Path, tmp_path: Path) -> None:
    """Each .py file in the project must have valid syntax."""
    cfile = str(tmp_path / (filepath.name + "c"))
    py_compile.compile(str(filepath), cfile=cfile, doraise=True)
