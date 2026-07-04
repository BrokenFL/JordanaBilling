from __future__ import annotations

from importlib import metadata


APPLICATION_NAME = "Jordana Billing"
GIT_COMMIT = "source-checkout"
BUILD_ID = "source-checkout"
RELEASE_LABEL = ""


def package_version() -> str:
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
