"""CLI entry point: ``python -m ephemeral_sites.cleanup``.

Opens the DB using settings from the environment, runs one cleanup pass,
exits with code 0. Invoked by the Kubernetes CronJob (step 15).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from ephemeral_sites import db
from ephemeral_sites.cleanup.runner import run_cleanup
from ephemeral_sites.config import get_settings


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    conn = db.open_db(Path(settings.db_path))
    try:
        result = run_cleanup(settings, conn)
    finally:
        conn.close()
    print(f"cleanup done: expired={len(result.expired_slugs)} purged_events={result.purged_events}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
