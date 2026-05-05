from __future__ import annotations
import signal
import sys
import time
import importlib
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import AppConfig
from .logging_config import log


class PipelineScheduler:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.scheduler = BackgroundScheduler()
        self._last_ingest = 0.0

    def _wait_ingest(self) -> None:
        """Gate ingestion with min_interval_minutes from config."""
        elapsed = time.time() - self._last_ingest
        if elapsed < self.cfg.min_interval_minutes * 60:
            wait = self.cfg.min_interval_minutes * 60 - elapsed
            log.info("Ingestion gated", extra={"wait_seconds": wait})
            return
        self._last_ingest = time.time()
        self._run_ingest()

    def _run_ingest(self) -> None:
        log.info("Scheduled: ingest")
        try:
            importlib.import_module("ingestion.ingest").main()
        except Exception as e:
            log.warning("Scheduled ingest failed", extra={"error": str(e)})

    def _run_intents(self) -> None:
        log.info("Scheduled: compute_intents")
        try:
            importlib.import_module("ingestion.compute_intents").classify_posts()
        except Exception as e:
            log.warning("Scheduled intents failed", extra={"error": str(e)})

    def _run_generate(self) -> None:
        log.info("Scheduled: generate_and_score")
        try:
            importlib.import_module("ingestion.generate_and_score").generate_and_score()
        except Exception as e:
            log.warning("Scheduled generate failed", extra={"error": str(e)})

    def _run_enrich(self) -> None:
        log.info("Scheduled: enrich")
        try:
            importlib.import_module("ingestion.enrich").enrich_once()
        except Exception as e:
            log.warning("Scheduled enrich failed", extra={"error": str(e)})

    def _run_once(self) -> None:
        """Full pipeline run (M1→M4)."""
        log.info("Scheduled: full pipeline run")
        self._run_ingest()
        self._run_intents()
        self._run_generate()
        self._run_enrich()

    def start(self, daemon: bool = False) -> None:
        if daemon:
            # Continuous mode: ingest every N minutes, intents/gen/enrich follow
            self.scheduler.add_job(
                self._run_once,
                IntervalTrigger(minutes=self.cfg.min_interval_minutes),
                id="full_pipeline",
            )
            log.info("Daemon started", extra={"interval_minutes": self.cfg.min_interval_minutes})

            def _shutdown(sig, frame):
                log.info("Shutdown signal received")
                self.scheduler.shutdown(wait=False)
                sys.exit(0)

            signal.signal(signal.SIGINT, _shutdown)
            signal.signal(signal.SIGTERM, _shutdown)

            self.scheduler.start()
            try:
                while True:
                    time.sleep(1)
            except (KeyboardInterrupt, SystemExit):
                self.scheduler.shutdown(wait=False)
        else:
            # Single run
            self._run_once()


def main() -> None:
    cfg = AppConfig.from_file()
    daemon = "--daemon" in sys.argv
    once = "--once" in sys.argv

    ps = PipelineScheduler(cfg)
    if daemon:
        ps.start(daemon=True)
    elif once:
        ps._run_once()
    else:
        # Default: full pipeline once
        ps._run_once()


if __name__ == "__main__":
    main()
