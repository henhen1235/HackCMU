#!/usr/bin/env python3
"""Launch EchoArena from the project root."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from echoarena.game import main


if __name__ == "__main__":
    main()
