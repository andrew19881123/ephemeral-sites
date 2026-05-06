"""Smoke tests for Step 1 scaffolding.

The roadmap (spec §16.1) mandates a ``test_smoke.py::test_imports_ok`` as the
green-check for Step 1. These tests verify the package is installable and the
CLI dispatcher behaves correctly for trivial invocations.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


def test_imports_ok():
    """The top-level package and subpackages must import without side effects."""
    import ephemeral_sites  # noqa: F401
    import ephemeral_sites.api  # noqa: F401
    import ephemeral_sites.cleanup  # noqa: F401
    import ephemeral_sites.server  # noqa: F401


def test_version_is_semver_like():
    """__version__ must be a non-empty string with at least two dots or digits."""
    from ephemeral_sites import __version__

    assert isinstance(__version__, str)
    assert __version__
    # Accept any dotted version or semver-like; we just reject obviously wrong values.
    assert any(ch.isdigit() for ch in __version__)


def test_main_version_subcommand_prints_version():
    """``python -m ephemeral_sites version`` must print the version and exit 0."""
    result = subprocess.run(
        [sys.executable, "-m", "ephemeral_sites", "version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ephemeral-sites" in result.stdout


def test_main_no_args_prints_usage_and_exits_nonzero():
    """Calling the CLI without a command is a usage error (exit 2)."""
    result = subprocess.run(
        [sys.executable, "-m", "ephemeral_sites"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Usage" in result.stderr


@pytest.mark.parametrize("cmd", ["api", "cleanup"])
def test_main_known_commands_not_yet_implemented(cmd):
    """Step 1: api/cleanup exit with a clear 'not implemented' message."""
    result = subprocess.run(
        [sys.executable, "-m", "ephemeral_sites", cmd],
        check=False,
        capture_output=True,
        text=True,
    )
    # Exit code 1 signals 'not implemented' (distinct from 2 = usage error).
    assert result.returncode == 1
    assert "not implemented" in result.stderr.lower()


def test_main_unknown_command_is_usage_error():
    """Unknown subcommands exit 2 (usage error) with a helpful message."""
    result = subprocess.run(
        [sys.executable, "-m", "ephemeral_sites", "does-not-exist"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Unknown command" in result.stderr
