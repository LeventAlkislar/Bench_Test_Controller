# bench_test/measurement/data_io.py
# -*- coding: utf-8 -*-
"""
Shared measurement data readers.

This module keeps CSV/xlsx parsing outside UI code so aggregation and graphing
use the same interpretation of measurement files.
"""

import glob
import os
from datetime import datetime, timedelta
from typing import List, Tuple

from bench_test.measurement.session import MEASUREMENT_MODE_CONTINUOUS_PAD

try:
    import openpyxl
    OPENPYXL_OK = True
except ImportError:
    OPENPYXL_OK = False


MAX_SAMPLES_PER_FILE = 5

CSV_SEPARATOR = ";"
CSV_DATA_START = 3
CSV_COL_TIME = 0
CSV_COL_CURRENT = 1


class MeasurementDataError(Exception):
    pass


def get_file_time(path: str) -> datetime:
    """Return the file creation time used as the measurement base time."""
    return datetime.fromtimestamp(os.path.getctime(path))


def collect_csv_files(measurements_dir: str) -> List[str]:
    """Return CSV files in measurement order."""
    pattern = os.path.join(measurements_dir, "*.csv")
    files = glob.glob(pattern)
    if not files:
        raise MeasurementDataError(f"CSV file not found:\n{measurements_dir}")
    files.sort(key=lambda p: os.path.getctime(p))
    return files


def collect_mtp_files(measurements_dir: str) -> List[str]:
    """Return DropSens PAD segment files in measurement order."""
    pattern = os.path.join(measurements_dir, "*.mtp")
    files = glob.glob(pattern)
    if not files:
        raise MeasurementDataError(f"DropSens PAD file not found:\n{measurements_dir}")
    files.sort(key=lambda p: os.path.getctime(p))
    return files


def parse_csv_file(
    path: str,
    time_offset: timedelta = timedelta(0),
) -> List[Tuple[datetime, float]]:
    """Read one DropView CSV file as ``(timestamp, current_uA)`` rows."""
    base_time = get_file_time(path) + time_offset
    rows: List[Tuple[datetime, float]] = []

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as exc:
        raise MeasurementDataError(f"Failed to read CSV: {path}\n{exc}")

    count = 0
    for line in lines[CSV_DATA_START:]:
        line = line.strip()
        if not line:
            continue
        if count >= MAX_SAMPLES_PER_FILE:
            break

        parts = line.split(CSV_SEPARATOR)
        if len(parts) < 2:
            continue

        try:
            time_s = float(parts[CSV_COL_TIME].strip().strip('"'))
            current = float(parts[CSV_COL_CURRENT].strip().strip('"'))
        except ValueError:
            continue

        rows.append((base_time + timedelta(seconds=time_s), current))
        count += 1

    return rows


def read_csv_measurement_series(
    measurements_dir: str,
    current_scale: float = 1.0,
    time_offset: timedelta = timedelta(0),
    skip_bad_files: bool = True,
) -> Tuple[List[float], List[float]]:
    """Read all CSV files as unix timestamps and scaled currents."""
    timestamps: List[float] = []
    currents: List[float] = []

    for path in collect_csv_files(measurements_dir):
        try:
            rows = parse_csv_file(path, time_offset=time_offset)
        except MeasurementDataError:
            if skip_bad_files:
                continue
            raise
        for ts, current in rows:
            timestamps.append(ts.timestamp())
            currents.append(current * current_scale)

    return timestamps, currents


def find_session_xlsx_path(session) -> str:
    """Return a session xlsx path from session.json or the measurements folder."""
    xlsx_path = session.get_file_path("xlsx")
    if xlsx_path and os.path.isfile(xlsx_path):
        return xlsx_path

    candidate = os.path.join(
        session.measurements_dir,
        f"{session.part_number}.xlsx",
    )
    return candidate if os.path.isfile(candidate) else ""


def read_xlsx_measurement_series(
    path: str,
    current_scale: float = 1.0,
) -> Tuple[List[float], List[float]]:
    """Read an aggregated xlsx file as unix timestamps and scaled currents."""
    if not OPENPYXL_OK:
        raise MeasurementDataError(
            "openpyxl library not found.\n"
            "Install it with: pip install openpyxl"
        )

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        timestamps: List[float] = []
        currents: List[float] = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            ts_val, cur_val = row[0], row[1]
            if ts_val is None or cur_val is None:
                continue
            if isinstance(ts_val, datetime):
                timestamps.append(ts_val.timestamp())
                currents.append(float(cur_val) * current_scale)
        wb.close()
        return timestamps, currents
    except Exception as exc:
        raise MeasurementDataError(f"Failed to read xlsx: {path}\n{exc}")


def read_session_measurement_series(
    session,
    current_scale: float = 1.0,
) -> Tuple[List[float], List[float]]:
    """Read session data, preferring xlsx and falling back to live CSV files."""
    xlsx_path = find_session_xlsx_path(session)
    if xlsx_path and OPENPYXL_OK:
        return read_xlsx_measurement_series(xlsx_path, current_scale=current_scale)

    if getattr(session, "measurement_mode", "") == MEASUREMENT_MODE_CONTINUOUS_PAD:
        try:
            return read_session_pad_segment_series(
                session.measurements_dir,
                current_scale=current_scale,
            )
        except MeasurementDataError:
            return [], []

    try:
        return read_csv_measurement_series(
            session.measurements_dir,
            current_scale=current_scale,
        )
    except MeasurementDataError:
        return [], []


def read_session_pad_segment_series(
    measurements_dir: str,
    current_scale: float = 1.0,
) -> Tuple[List[float], List[float]]:
    """Read all AutoSave PAD .mtp segment files as one time-aware series."""
    # Local import avoids a module cycle: dropsens_io reuses MeasurementDataError.
    from bench_test.measurement.dropsens_io import read_pad_measurement

    timestamps: List[float] = []
    currents: List[float] = []

    for path in collect_mtp_files(measurements_dir):
        measurement = read_pad_measurement(path)
        if not measurement.curves:
            continue
        curve = measurement.curves[0]
        times = curve.points.get("time", [])
        currents_ua = curve.points.get("i1", [])
        total = min(len(times), len(currents_ua))
        for idx in range(total):
            ts = measurement.base_time + timedelta(seconds=times[idx])
            timestamps.append(ts.timestamp())
            currents.append(currents_ua[idx] * current_scale)

    if not timestamps:
        raise MeasurementDataError(f"No PAD segment data found:\n{measurements_dir}")

    pairs = sorted(zip(timestamps, currents), key=lambda item: item[0])
    return [item[0] for item in pairs], [item[1] for item in pairs]
