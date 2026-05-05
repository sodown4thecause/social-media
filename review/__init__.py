from __future__ import annotations
import json
import sys
import time
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.db import connect, init_db


def db_path() -> str:
    return str(ROOT / "data.sqlite3")


def get_con() -> sqlite3.Connection:
    con = connect(db_path())
    init_db(con)
    con.row_factory = sqlite3.Row
    return con
