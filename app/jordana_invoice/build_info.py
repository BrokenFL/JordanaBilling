from __future__ import annotations

from importlib import metadata
from pathlib import Path
import tomllib


APPLICATION_NAME = "Jordana Billing"
GIT_COMMIT = "source-checkout"
BUILD_ID = "source-checkout"
RELEASE_LABEL = ""


def source_tree_version() -> str | None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    version = data.get("project", {}).get("version")
    return str(version) if version else None


def package_version() -> str:
    if GIT_COMMIT == "source-checkout":
        source_version = source_tree_version()
        if source_version:
            return source_version
    try:
        return metadata.version("jordana-invoice")
    except metadata.PackageNotFoundError:
        return "0.0.0+source"


def current_build_info() -> dict[str, str]:
    return {
        "application": APPLICATION_NAME,
        "package": "jordana-invoice",
        "version": package_version(),
        "git_commit": GIT_COMMIT,
        "build_id": BUILD_ID,
        "release_label": RELEASE_LABEL,
    }
