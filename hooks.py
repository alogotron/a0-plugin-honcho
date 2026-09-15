"""Automatic lifecycle for the Honcho plugin."""

from __future__ import annotations

import importlib
from pathlib import Path
import subprocess
import sys

_REQUIREMENTS = Path(__file__).with_name("requirements.txt")


def _helper():
    for path in (
        "usr.plugins.honcho.helpers.honcho_helper",
        "plugins.honcho.helpers.honcho_helper",
        "helpers.honcho_helper",
    ):
        try:
            return importlib.import_module(path)
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError("Honcho helper is not importable")


def install() -> None:
    """Install dependencies and run idempotent local-state migrations."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(_REQUIREMENTS)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Honcho dependency installation failed with exit code {result.returncode}"
        )
    # The installer purges plugin modules after this hook returns. Starting a
    # thread here could orphan its module-level singleton registry and allow an
    # agent-init hook to create a duplicate worker. Agent init owns startup.
    _helper().initialize_runtime(start_worker=False)


def pre_update() -> None:
    """Drain briefly and stop plugin-owned worker threads before code replacement."""
    try:
        _helper().stop_runtime(drain_seconds=5)
    except (ImportError, AttributeError):
        pass


def uninstall() -> None:
    """Stop workers while preserving remote memory, shared packages, and outbox."""
    pre_update()
