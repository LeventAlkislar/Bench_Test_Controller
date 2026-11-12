"""Non-blocking CSV activity probe; content checks issue advisory warnings only."""
import math
import os
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from bench_test.csv_progress import inspect_csv_progress


CSV_SCAN_INTERVAL = 60


def read_csv_expectation(script_path, directory):
    """Estimate export timing only for the packaged RUN/EXPORT/WAIT PAD script."""
    if not script_path or not directory:
        return None
    try:
        script = Path(script_path).resolve()
        actions = ET.parse(script).getroot().findall("./actions/action")
        if [action.get("type") for action in actions] != ["LOADMETHOD", "REPEAT", "RUN", "EXPORTCURVES", "WAIT"]:
            return None
        load, repeat, run, export, wait = actions
        if any(action.findtext("nodes") != "0" for action in (load, run, export)):
            return None
        exports = int(repeat.findtext("times", "0"))
        if exports < 1 or repeat.findtext("orders") != "3":
            return None
        method_path = Path(load.findtext("file", ""))
        csv_path = Path(export.findtext("file", ""))
        if (not method_path.is_absolute() or method_path.resolve().parent != script.parent
                or not csv_path.is_absolute() or csv_path.suffix.lower() != ".csv"
                or csv_path.resolve().parent != Path(directory).resolve()):
            return None
        technic = ET.parse(method_path).getroot().find("technic")
        if technic is None or technic.get("id") != "PAD":
            return None
        duration = float(technic.findtext("commonUserParameters/parameter[@id='t']", "nan"))
        pretreatment = [float(technic.findtext(f"pretreatmentUserParameters/parameter[@id='{name}']", "0"))
                        for name in ("tcond", "tdep", "tequil")]
        wait_seconds = int(wait.get("timeMS", "-1")) / 1000
        if duration <= 0 or any(not math.isfinite(value) or value < 0
                                for value in (duration, *pretreatment, wait_seconds)):
            return None
        first = duration + sum(pretreatment)
        period = first + wait_seconds
        if not math.isfinite(period):
            return None
        return {"first_seconds": first, "interval_seconds": period, "exports": exports,
                "csv_stem": csv_path.stem.casefold()}
    except (OSError, ValueError, OverflowError, ET.ParseError, RuntimeError):
        return None


class CsvProgressProbe:
    def __init__(self):
        self.sample = None
        self.content_cache = None
        self.lock = threading.Lock()
        self.reset()

    def reset(self, key=None, started=0):
        """Discard one run's observations while retaining the scan thread lock."""
        self.key, self.started = key, started
        self.last_request, self.last_change = -1000, None
        self.last_successful_scan = None
        self.signature, self.intervals, self.processed_sample = None, [], None
        self.first_miss_at = self.late_export_at = None
        self.content_position = self.content_last_progress = self.content_notice = None
        self.content_last_progress_ns = None
        self.content_failures = self.storage_failures = 0

    def scan(self, directory, key, sampled_at, started_ns, csv_stem):
        try:
            latest, count = None, 0
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_file() and entry.name.lower().endswith(".csv"):
                        stem = entry.name[:-4].casefold()
                        if csv_stem and stem != csv_stem and not stem.startswith(csv_stem + "-"):
                            continue
                        stat = entry.stat()
                        if stat.st_size == 0 or (started_ns and stat.st_mtime_ns < started_ns):
                            continue
                        candidate = (stat.st_mtime_ns, stat.st_size, entry.name)
                        latest = max(latest, candidate) if latest else candidate
                        count += 1
            content = None
            if latest:
                cache_key = (key, latest)
                if self.content_cache and self.content_cache[0] == cache_key:
                    content = self.content_cache[1]
                else:
                    mtime_ns, size, name = latest
                    content = inspect_csv_progress(os.path.join(directory, name), mtime_ns, size)
                    if content[0] != "unverified":
                        self.content_cache = (cache_key, content)
            self.sample = {"key": key, "at": sampled_at, "signature": (latest, count) if count else None,
                           "content": content}
        except OSError as exc:
            self.sample = {"key": key, "at": sampled_at, "error": str(exc)}
        finally:
            self.lock.release()

    def observe(self, directory, epoch, now, active=True, expectation=None, started_ns=None,
                *, scan_interval=CSV_SCAN_INTERVAL):
        csv_stem = expectation.get("csv_stem") if expectation else None
        key = (directory, epoch, started_ns, csv_stem) if active and directory else None
        if key != self.key:
            started = epoch if expectation and isinstance(epoch, (int, float)) and 0 < epoch <= now else now
            self.reset(key, started)
        if key is None:
            return None, None
        if now - self.last_request >= scan_interval and self.lock.acquire(blocking=False):
            self.last_request = now
            threading.Thread(target=self.scan, args=(directory, key, now, started_ns, csv_stem), daemon=True).start()
        sample = self.sample
        if sample and sample.get("key") == key:
            if sample.get("error"):
                if sample["at"] != self.processed_sample:
                    self.processed_sample = sample["at"]
                    self.storage_failures += 1
                self.first_miss_at = None
                self.late_export_at = None
                self.content_failures = 0
                return None, "measurement_storage_unavailable"
            if now - sample["at"] > 300:
                self.first_miss_at = None
                self.late_export_at = None
                self.content_failures = 0
                return None, "measurement_probe_not_responding"
            if sample["at"] != self.processed_sample:
                self.processed_sample = sample["at"]
                self.last_successful_scan = sample["at"]
                self.storage_failures = 0
                signature = sample.get("signature")
                if signature is not None and (self.signature is None or signature > self.signature):
                    if self.last_change is not None:
                        self.intervals.append(sample["at"] - self.last_change)
                        self.intervals = self.intervals[-10:]
                    self.last_change, self.signature = sample["at"], signature
                    self.late_export_at = None
                self._observe_content(sample, expectation)
            if expectation:
                fault, notice = self._expected_progress(sample, expectation)
                return fault, notice or self.content_notice
        elif expectation:
            if now - self.started > 300:
                return None, "measurement_probe_not_responding"
            return None, None
        if len(self.intervals) >= 2 and self.last_change is not None:
            if now - self.last_change > max(1200, 5 * max(self.intervals)):
                return "discrete_data_stalled", None
        if now - self.started > 1800 and len(self.intervals) < 2:
            return None, "discrete_data_not_verified"
        return None, self.content_notice

    def _observe_content(self, sample, expectation):
        content = sample.get("content")
        self.content_notice = None
        if content is None:
            self.content_failures = 0
            return
        state, position = content
        if state != "valid":
            self.content_failures += 1
            if self.content_failures >= 2:
                self.content_notice = ("measurement_csv_invalid" if state == "invalid"
                                       else "measurement_csv_content_unverified")
            return
        self.content_failures = 0
        previous = self.content_position
        if (previous is None or position[0] != previous[0]
                or (position[1] is not None and previous[1] is not None and position[1] > previous[1])
                or (position[1] == previous[1] and position[2] > previous[2])):
            self.content_position = position
            self.content_last_progress = sample["at"]
            self.content_last_progress_ns = sample["signature"][0][0]
            return
        period = expectation["interval_seconds"] if expectation else (max(self.intervals) if len(self.intervals) >= 2 else None)
        if (period and self.last_change > self.content_last_progress
                and sample["at"] - self.content_last_progress >= 2 * period):
            self.content_notice = "measurement_csv_records_not_advancing"

    def _expected_progress(self, sample, expectation):
        sampled_at = sample["at"]
        if self.last_change is None:
            first = expectation["first_seconds"]
            if sampled_at - self.started < first + max(CSV_SCAN_INTERVAL, first * 0.25):
                return None, None
            if self.first_miss_at is None:
                self.first_miss_at = sampled_at
            if sampled_at > self.first_miss_at:
                return "discrete_first_export_missing", None
            return None, "discrete_first_export_late"
        if self.signature[1] >= expectation["exports"]:
            return None, None
        period = expectation["interval_seconds"]
        grace = max(CSV_SCAN_INTERVAL, period * 0.25)
        elapsed = sampled_at - self.last_change
        if elapsed >= period + grace:
            if self.late_export_at is None:
                self.late_export_at = sampled_at
            if elapsed >= 2 * period + grace and sampled_at > self.late_export_at:
                return "discrete_data_stalled", None
            return None, "discrete_export_late"
        return None, None
