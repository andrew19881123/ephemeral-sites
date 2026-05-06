"""Entry point: ``python -m ephemeral_sites [api|cleanup]``.

Step 1 scaffolding: the actual API and cleanup processes are not implemented
yet. This module only parses the subcommand and prints a banner so that the
container can be built and smoke-tested end-to-end.
"""

from __future__ import annotations

import sys

from . import __version__


def _usage() -> str:
    return (
        "Usage: python -m ephemeral_sites <command>\n"
        "\n"
        "Commands:\n"
        "  api       Run the REST API + static server (not yet implemented)\n"
        "  cleanup   Run the expired-sites cleanup job (not yet implemented)\n"
        "  version   Print version and exit\n"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI dispatcher. Returns the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(_usage())
        return 2

    cmd = args[0]
    if cmd == "version":
        print(f"ephemeral-sites {__version__}")
        return 0
    if cmd in {"api", "cleanup"}:
        # Scaffolding placeholder - real implementations arrive in later steps.
        sys.stderr.write(
            f"ephemeral-sites {__version__}: '{cmd}' is not implemented yet.\n"
            "This build is the Step 1 scaffolding only.\n"
        )
        return 1

    sys.stderr.write(f"Unknown command: {cmd!r}\n\n")
    sys.stderr.write(_usage())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
