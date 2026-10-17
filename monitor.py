"""One shared collection loop; HTTP requests only read its latest snapshot."""
from copy import deepcopy
from datetime import datetime, timezone
import logging
from threading import Event, Lock, Thread

from collector import collect_snapshot, utc_now


class Monitor:
    def __init__(self, config, collector=collect_snapshot):
        self.config = config
        self.collector = collector
        self._lock = Lock()
        self._stop = Event()
        self._thread = None
        self._data = {"devices": [], "errors": [], "updated_at": None, "duration_seconds": None, "collecting": False}

    def refresh(self):
        with self._lock:
            if self._data["collecting"]:
                return
            self._data["collecting"] = True
            previous = deepcopy(self._data["devices"])
        try:
            snapshot = self.collector(self.config, previous)
            with self._lock:
                self._data.update(snapshot)
        except Exception:
            logging.getLogger(__name__).exception("Collection cycle failed")
            with self._lock:
                self._data["errors"] = ["Collection failed; previous data retained. Check the collector console and configuration."]
        finally:
            with self._lock:
                self._data["collecting"] = False
                self._data["last_attempt_at"] = utc_now()

    def snapshot(self):
        with self._lock:
            data = deepcopy(self._data)
        updated = data["updated_at"]
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(updated)).total_seconds() if updated else None
        data.update(
            stale=age is None or age > max(self.config["refresh_seconds"] * 2, 60),
            age_seconds=round(age, 1) if age is not None else None,
            refresh_seconds=self.config["refresh_seconds"],
            domain=self.config["domain"],
            networks=self.config["networks"],
            discovery_dc=self.config["domain_controller"],
        )
        return data

    def _run(self):
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self.config["refresh_seconds"])

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="lab-collector", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
