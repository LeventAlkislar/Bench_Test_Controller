"""Bounded CSV progress checks shared by BTC and WD; standard library only."""
import csv
import io
import math
import os
import re


CSV_SEPARATOR = ";"
CSV_COL_TIME = 0
CSV_COL_CURRENT = 1
CSV_WATCHDOG_READ_LIMIT = 256 * 1024


def inspect_csv_progress(path, expected_mtime_ns, expected_size):
    """Read a bounded, stable DropView export without interpreting current values."""
    if expected_size > CSV_WATCHDOG_READ_LIMIT:
        return "unverified", None
    try:
        with open(path, "rb") as stream:
            content = stream.read(CSV_WATCHDOG_READ_LIMIT + 1)
            stat = os.fstat(stream.fileno())
        if (len(content) != expected_size or stat.st_mtime_ns != expected_mtime_ns
                or stat.st_size != expected_size):
            return "unverified", None
        rows = csv.reader(io.StringIO(content.decode("utf-8-sig", errors="replace")),
                          delimiter=CSV_SEPARATOR, strict=True)
        curve = next(rows, [])
        next(rows, [])
        header = [value.strip().casefold().replace("μ", "u").replace("µ", "u")
                  for value in next(rows, [])[:2]]
        if header != ["time (s)", "current (ua)"]:
            return "unverified", None
        last_time = None
        for row in rows:
            if not any(value.strip() for value in row):
                continue
            if len(row) < 2:
                return "invalid", None
            time_s, current = float(row[CSV_COL_TIME]), float(row[CSV_COL_CURRENT])
            if (not math.isfinite(time_s) or not math.isfinite(current) or time_s < 0
                    or (last_time is not None and time_s <= last_time)):
                return "invalid", None
            last_time = time_s
        if last_time is None:
            return "invalid", None
        curve_number = re.fullmatch(r"(.+)#([0-9]{1,12})", curve[0].strip()) if curve else None
        if curve_number:
            return "valid", (curve_number[1], int(curve_number[2]), last_time)
        return "valid", (os.path.basename(path), None, last_time)
    except OSError:
        return "unverified", None
    except (ValueError, csv.Error):
        return "invalid", None
