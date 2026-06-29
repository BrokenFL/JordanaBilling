from __future__ import annotations

from .util import text


PAST_CAPTURE_WINDOWS = frozenset(
    {
        "past_3_days",
        "past_7_days",
        "backfill_2026_06_01_through_2026_06_14",
    }
)
FUTURE_CAPTURE_WINDOWS = frozenset({"next_7_days", "next_2_days"})
BACKFILL_CAPTURE_WINDOWS = frozenset({"backfill_2026_06_01_through_2026_06_14"})
SUPPORTED_CAPTURE_WINDOWS = PAST_CAPTURE_WINDOWS | FUTURE_CAPTURE_WINDOWS | frozenset({"legacy"})
DEPRECATED_CAPTURE_WINDOWS = frozenset({"past_7_days", "next_2_days", "legacy"})


def normalize_capture_window(value: object) -> str:
    return text(value)


def is_past_capture_window(value: object) -> bool:
    return normalize_capture_window(value) in PAST_CAPTURE_WINDOWS


def is_future_capture_window(value: object) -> bool:
    return normalize_capture_window(value) in FUTURE_CAPTURE_WINDOWS


def is_backfill_capture_window(value: object) -> bool:
    return normalize_capture_window(value) in BACKFILL_CAPTURE_WINDOWS


def is_supported_capture_window(value: object) -> bool:
    return normalize_capture_window(value) in SUPPORTED_CAPTURE_WINDOWS


def completed_run_windows(windows: set[str]) -> bool:
    """Return true when a run has coherent completed capture evidence.

    Normal recurring runs complete when they contain any supported past batch and
    any supported future batch. One-time backfills are intentionally past-only
    evidence, so a supported backfill label is complete by itself.
    """
    normalized = {normalize_capture_window(window) for window in windows}
    if any(is_backfill_capture_window(window) for window in normalized):
        return True
    return any(is_past_capture_window(window) for window in normalized) and any(
        is_future_capture_window(window) for window in normalized
    )
