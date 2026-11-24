"""Docker healthcheck: exit 0 if the heartbeat file is fresh, 1 otherwise."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

MAX_AGE_SECONDS = 60.0


def is_healthy(path: Path, now: float | None = None, max_age: float = MAX_AGE_SECONDS) -> bool:
    try:
        written = float(path.read_text().strip())
    except (OSError, ValueError):
        return False
    return ((now if now is not None else time.time()) - written) < max_age


def main() -> None:
    path = Path(os.environ.get("HEARTBEAT_PATH", "/data/heartbeat"))
    sys.exit(0 if is_healthy(path) else 1)


if __name__ == "__main__":
    main()
