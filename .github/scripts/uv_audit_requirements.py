#!/usr/bin/env python3
"""Run `uv audit` against this repository's requirements files.

Odysseus still treats requirements.txt as the dependency source of truth. Since
`uv audit` operates on uv projects, this script builds a temporary pyproject
from the requirements files and audits that project without writing repository
state.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REQUIREMENTS = (
    ROOT / "requirements.txt",
    ROOT / "requirements-optional.txt",
)


def _strip_inline_comment(line: str) -> str:
    for marker in (" #", "\t#"):
        index = line.find(marker)
        if index != -1:
            return line[:index].strip()
    return line


def _read_requirements(path: Path, seen: set[Path] | None = None) -> list[str]:
    seen = seen or set()
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)

    requirements: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        line = _strip_inline_comment(line)
        if not line:
            continue

        if line.startswith(("-r ", "--requirement ")):
            _, nested = line.split(maxsplit=1)
            requirements.extend(_read_requirements(path.parent / nested, seen))
            continue

        if line.startswith(("-", "--")):
            raise SystemExit(f"Unsupported requirements option in {path}: {line}")

        requirements.append(line)

    return requirements


def _toml_string_array(values: list[str]) -> str:
    if not values:
        return "[]"
    inner = ",\n    ".join(json.dumps(value) for value in values)
    return f"[\n    {inner},\n]"


def _write_audit_project(project_dir: Path, core: list[str], optional: list[str]) -> None:
    pyproject = project_dir / "pyproject.toml"
    pyproject.write_text(
        "\n".join(
            [
                "[project]",
                'name = "odysseus-uv-audit"',
                'version = "0.0.0"',
                'requires-python = ">=3.11"',
                f"dependencies = {_toml_string_array(core)}",
                "",
                "[project.optional-dependencies]",
                f"optional = {_toml_string_array(optional)}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python-version",
        default="3.11",
        help="Python version to resolve and audit against.",
    )
    parser.add_argument(
        "--python-platform",
        default="linux",
        help="Python platform to resolve and audit against.",
    )
    parser.add_argument(
        "uv_args",
        nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to `uv audit` after `--`.",
    )
    args = parser.parse_args()
    forwarded = args.uv_args[1:] if args.uv_args[:1] == ["--"] else args.uv_args

    core_requirements = _read_requirements(DEFAULT_REQUIREMENTS[0])
    optional_requirements = _read_requirements(DEFAULT_REQUIREMENTS[1])

    with tempfile.TemporaryDirectory(prefix="odysseus-uv-audit-") as tmp:
        project_dir = Path(tmp)
        _write_audit_project(project_dir, core_requirements, optional_requirements)
        command = [
            "uv",
            "--project",
            str(project_dir),
            "--preview-features",
            "audit-command",
            "audit",
            "--python-version",
            args.python_version,
            "--python-platform",
            args.python_platform,
            *forwarded,
        ]
        return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
