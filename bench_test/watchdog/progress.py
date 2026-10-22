"""Non-blocking CSV activity probe. No CSV content or electrochemical inference."""
import os
import threading


class CsvProgressProbe:
    def __init__(self):
        self.key = None
        self.started = 0
        self.last_request = -1000
        self.last_change = None
        self.signature = None
        self.intervals = []
        self.sample = None
        self.processed_sample = None
        self.lock = threading.Lock()

    def scan(self, directory, key, sampled_at):
        try:
            latest, count = None, 0
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_file() and entry.name.lower().endswith(".csv"):
                        stat = entry.stat()
                        candidate = (stat.st_mtime_ns, stat.st_size, entry.name)
                        latest = max(latest, candidate) if latest else candidate
                        count += 1
            self.sample = {"key": key, "at": sampled_at, "signature": (latest, count) if count else None}
        except OSError as exc:
            self.sample = {"key": key, "at": sampled_at, "error": str(exc)}
        finally:
            self.lock.release()

    def observe(self, directory, epoch, now, active=True):
        key = (directory, epoch) if active and directory else None
        if key != self.key:
            self.key, self.started = key, now
            self.last_request, self.last_change = -1000, None
            self.signature, self.intervals, self.processed_sample = None, [], None
        if key is None:
            return None, None
        if now - self.last_request >= 60 and self.lock.acquire(blocking=False):
            self.last_request = now
            threading.Thread(target=self.scan, args=(directory, key, now), daemon=True).start()
        sample = self.sample
        if sample and sample.get("key") == key:
            if sample.get("error"):
                return None, "measurement_storage_unavailable"
            if sample["at"] != self.processed_sample:
                self.processed_sample = sample["at"]
                signature = sample.get("signature")
                if signature is not None and signature != self.signature:
                    if self.last_change is not None:
                        self.intervals.append(sample["at"] - self.last_change)
                        self.intervals = self.intervals[-10:]
                    self.last_change, self.signature = sample["at"], signature
            if now - sample["at"] > 300:
                return None, "measurement_probe_not_responding"
        if len(self.intervals) >= 2 and self.last_change is not None:
            if now - self.last_change > max(1200, 5 * max(self.intervals)):
                return "discrete_data_stalled", None
        if now - self.started > 1800 and len(self.intervals) < 2:
            return None, "discrete_data_not_verified"
        return None, None
