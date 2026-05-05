from __future__ import annotations

import sys
import traceback
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(ROOT))

with (LOG_DIR / "webapp.persistent.log").open("a", encoding="utf-8") as log:
    sys.stdout = log
    sys.stderr = log
    try:
        port = os.getenv("FLOWINTENT_WEBAPP_PORT")
        if port:
            sys.argv = ["webapp.server", "--host", "127.0.0.1", "--port", port]
        from webapp.server import main

        main()
    except Exception:
        traceback.print_exc()
        raise
