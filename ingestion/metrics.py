from __future__ import annotations
import json
import time
from threading import Lock
from typing import Dict

_mutex = Lock()
_counters: Dict[str, float] = {}
_gauges: Dict[str, float] = {}
_last_run: Dict[str, float] = {}


def incr(name: str, delta: float = 1.0) -> None:
    with _mutex:
        _counters[name] = _counters.get(name, 0.0) + delta


def set_gauge(name: str, value: float) -> None:
    with _mutex:
        _gauges[name] = value


def record_run(stage: str) -> None:
    with _mutex:
        _last_run[stage] = time.time()


def dump() -> dict:
    with _mutex:
        return {
            "counters": dict(_counters),
            "gauges": dict(_gauges),
            "last_runs": {k: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(v)) for k, v in _last_run.items()},
        }


def dump_json() -> str:
    return json.dumps(dump(), indent=2)


def main() -> None:
    print(dump_json())


if __name__ == "__main__":
    main()
