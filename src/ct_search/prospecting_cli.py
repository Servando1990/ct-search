from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _outreach_project() -> Path:
    candidates = [
        Path.home() / "Servando/controlthrive/internal/outreach",
        Path.home() / "servando/controlthrive/internal/outreach",
    ]
    for candidate in candidates:
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError(
        "Could not find the outreach prospecting project. Expected one of: "
        + ", ".join(str(candidate) for candidate in candidates)
    )


def _normalize_args(argv: list[str]) -> list[str]:
    normalized = list(argv)
    for index, value in enumerate(normalized[:-1]):
        if value in {"--config", "--review-json"}:
            candidate = Path(normalized[index + 1])
            if not candidate.is_absolute():
                normalized[index + 1] = str((Path.cwd() / candidate).resolve())
    return normalized


def main() -> int:
    project = _outreach_project()
    command = [
        "uv",
        "run",
        "--project",
        str(project),
        "prospect-engine",
        *_normalize_args(sys.argv[1:]),
    ]
    return subprocess.call(command, cwd=Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main())
