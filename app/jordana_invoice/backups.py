from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .build_info import current_build_info


DEFAULT_APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Jordana Billing"
DEFAULT_PRIMARY_BACKUP_DIR = DEFAULT_APP_SUPPORT / "backups"
DEFAULT_SECONDARY_BACKUP_DIR = Path.home() / "Documents" / "Jordana Billing Private Backups"
DEFAULT_PRIVATE_CONFIG_DIR = DEFAULT_APP_SUPPORT / "config"
DEFAULT_BUSY_TIMEOUT_MS = 5000


@dataclass(frozen=True)
class BackupResult:
    backup_path: Path
    manifest_path: Path
    integrity_status: str
    sha256: str
    size_bytes: int
    secondary_path: Path | None
    secondary_status: str


def primary_backup_dir() -> Path:
    override = os.environ.get("JORDANA_BACKUP_DIR")
    if override:
        return Path(os.path.expanduser(override))
    return DEFAULT_PRIMARY_BACKUP_DIR


def secondary_backup_dir() -> Path | None:
    override = os.environ.get("JORDANA_SECONDARY_BACKUP_DIR")
    if override:
        return Path(os.path.expanduser(override))
    if DEFAULT_SECONDARY_BACKUP_DIR.exists() and os.access(DEFAULT_SECONDARY_BACKUP_DIR, os.W_OK):
        return DEFAULT_SECONDARY_BACKUP_DIR
    return None


def backup_private_config_source() -> Path | None:
    configured = os.environ.get("JORDANA_PRIVATE_CONFIG_PATH")
    if configured:
        path = Path(os.path.expanduser(configured))
        return path if path.exists() else None
    if DEFAULT_PRIVATE_CONFIG_DIR.exists():
        return DEFAULT_PRIVATE_CONFIG_DIR
    root_env = Path.cwd() / ".env"
    if root_env.exists():
        return root_env
    return None


def create_verified_backup(
    db_path: str | Path,
    *,
    reason: str,
    protected: bool = False,
    allow_secondary: bool = True,
    run_retention: bool = True,
) -> BackupResult:
    source = Path(db_path).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"Database not found at {source}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = primary_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_reason = _safe_slug(reason)
    backup_path = _unique_path(backup_dir / f"{source.stem}.backup-{safe_reason}-{timestamp}{source.suffix}")

    _sqlite_backup(source, backup_path)
    integrity = verify_sqlite_backup(backup_path)
    if integrity != "ok":
        backup_path.unlink(missing_ok=True)
        raise RuntimeError(f"Backup integrity check failed: {integrity}")

    config_backup_path = _backup_private_config(backup_path, backup_dir)
    digest = _sha256(backup_path)
    size = backup_path.stat().st_size
    secondary_path, secondary_status = (None, "not_configured")
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".manifest.json")

    manifest: dict[str, Any] = {
        "timestamp": timestamp,
        "source_db_path": str(source.resolve()),
        "backup_path": str(backup_path),
        "app": current_build_info(),
        "reason": reason,
        "size_bytes": size,
        "sha256": digest,
        "integrity_status": integrity,
        "protected": bool(protected),
        "private_config_backup_path": str(config_backup_path) if config_backup_path else None,
        "secondary_copy_status": secondary_status,
        "secondary_backup_path": None,
    }

    if allow_secondary:
        secondary_path, secondary_status = _copy_to_secondary(backup_path, manifest)
        manifest["secondary_copy_status"] = secondary_status
        manifest["secondary_backup_path"] = str(secondary_path) if secondary_path else None

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if allow_secondary and secondary_path:
        secondary_manifest = secondary_path.with_suffix(secondary_path.suffix + ".manifest.json")
        secondary_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if run_retention:
        cleanup_old_backups(backup_dir)

    return BackupResult(
        backup_path=backup_path,
        manifest_path=manifest_path,
        integrity_status=integrity,
        sha256=digest,
        size_bytes=size,
        secondary_path=secondary_path,
        secondary_status=secondary_status,
    )


def maybe_create_daily_launch_backup(db_path: str | Path) -> BackupResult | None:
    source = Path(db_path).expanduser()
    if not source.exists():
        return None
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    latest = latest_backup_manifest(source)
    if latest and str(latest.get("timestamp", "")).startswith(today):
        return None
    return create_verified_backup(source, reason="app_launch_daily")


def backup_status(db_path: str | Path | None = None) -> dict[str, Any]:
    manifest = latest_backup_manifest(db_path)
    return {
        "ok": True,
        "primary_backup_dir": str(primary_backup_dir()),
        "secondary_backup_dir": str(secondary_backup_dir()) if secondary_backup_dir() else "",
        "last_backup_time": manifest.get("timestamp", "") if manifest else "",
        "last_backup_path": manifest.get("backup_path", "") if manifest else "",
        "integrity_status": manifest.get("integrity_status", "unknown") if manifest else "unknown",
        "secondary_copy_status": manifest.get("secondary_copy_status", "unknown") if manifest else "unknown",
        "source_db_path": str(Path(db_path).expanduser()) if db_path else "",
    }


def latest_backup_manifest(db_path: str | Path | None = None) -> dict[str, Any] | None:
    source_path = str(Path(db_path).expanduser().resolve()) if db_path else None
    manifests = sorted(primary_backup_dir().glob("*.manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in manifests:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if source_path and data.get("source_db_path") != source_path:
            continue
        return data
    return None


def open_backup_folder() -> dict[str, Any]:
    folder = primary_backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(["open", str(folder)])
    except Exception as error:
        raise RuntimeError("Could not open backup folder.") from error
    return {"ok": True, "path": str(folder)}


def verify_sqlite_backup(path: str | Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        return str(result[0]) if result else "unknown"
    finally:
        conn.close()


def cleanup_old_backups(folder: str | Path | None = None) -> int:
    backup_dir = Path(folder) if folder else primary_backup_dir()
    manifests = []
    for path in backup_dir.glob("*.manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("protected"):
            continue
        backup_path = Path(data.get("backup_path") or "")
        timestamp = str(data.get("timestamp") or "")
        if backup_path.exists() and timestamp:
            manifests.append((timestamp, backup_path, path))
    manifests.sort(reverse=True)
    keep: set[Path] = set()
    keep.update(path for _, path, _ in manifests[:14])
    daily: set[str] = set()
    weekly: set[tuple[int, int]] = set()
    monthly: set[str] = set()
    for timestamp, backup_path, _manifest_path in manifests:
        dt = _parse_backup_timestamp(timestamp)
        if not dt:
            continue
        day_key = dt.strftime("%Y-%m-%d")
        week_key = dt.isocalendar()[:2]
        month_key = dt.strftime("%Y-%m")
        if len(daily) < 30 and day_key not in daily:
            keep.add(backup_path)
            daily.add(day_key)
        if len(weekly) < 12 and week_key not in weekly:
            keep.add(backup_path)
            weekly.add(week_key)
        if len(monthly) < 12 and month_key not in monthly:
            keep.add(backup_path)
            monthly.add(month_key)
    deleted = 0
    for _timestamp, backup_path, manifest_path in manifests:
        if backup_path in keep:
            continue
        backup_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        config_path = backup_path.with_suffix(backup_path.suffix + ".private-config")
        if config_path.exists():
            if config_path.is_dir():
                shutil.rmtree(config_path)
            else:
                config_path.unlink(missing_ok=True)
        deleted += 1
    return deleted


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    source_conn = sqlite3.connect(str(source_path), timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000.0)
    destination_conn = sqlite3.connect(str(destination_path), timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000.0)
    try:
        source_conn.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
        destination_conn.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
        source_conn.backup(destination_conn)
        destination_conn.commit()
    finally:
        destination_conn.close()
        source_conn.close()


def _backup_private_config(backup_path: Path, backup_dir: Path) -> Path | None:
    source = backup_private_config_source()
    if not source:
        return None
    target = backup_path.with_suffix(backup_path.suffix + ".private-config")
    if source.is_dir():
        ignore = shutil.ignore_patterns("*.log", "*.sqlite*", "backups", "runtime")
        shutil.copytree(source, target, ignore=ignore, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)
    return target


def _copy_to_secondary(backup_path: Path, manifest: dict[str, Any]) -> tuple[Path | None, str]:
    folder = secondary_backup_dir()
    if not folder:
        return None, "not_configured"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not os.access(folder, os.W_OK):
            return None, "not_writable"
        target = _unique_path(folder / backup_path.name)
        shutil.copy2(backup_path, target)
        if _sha256(target) != manifest["sha256"]:
            target.unlink(missing_ok=True)
            return None, "checksum_failed"
        config_source = backup_path.with_suffix(backup_path.suffix + ".private-config")
        if config_source.exists():
            config_target = target.with_suffix(target.suffix + ".private-config")
            if config_source.is_dir():
                shutil.copytree(config_source, config_target, dirs_exist_ok=True)
            else:
                shutil.copy2(config_source, config_target)
        return target, "copied"
    except Exception:
        return None, "failed"


def _safe_slug(value: str) -> str:
    raw = "".join(ch if ch.isalnum() else "-" for ch in str(value or "backup").lower())
    return "-".join(part for part in raw.split("-") if part)[:48] or "backup"


def _unique_path(path: Path) -> Path:
    candidate = path
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        counter += 1
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_backup_timestamp(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
