# bench_test/measurement/dropsens_io.py
# -*- coding: utf-8 -*-
"""
DropSens XML-based measurement readers.

The first supported viewer path is PAD (.mtp): read metadata and points, then
return a timestamp/current series compatible with the existing Viewer graph.
"""

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List

from bench_test.measurement.data_io import MeasurementDataError, get_file_time
from bench_test.measurement.log_parser import LogParser


@dataclass
class DropSensCurve:
    name: str
    title: str
    technique: str
    parameters: Dict[str, Dict[str, str]] = field(default_factory=dict)
    points: Dict[str, List[float]] = field(default_factory=dict)
    overload: List[bool] = field(default_factory=list)


@dataclass
class DropSensMeasurement:
    path: str
    title: str
    part_number: str
    device: str
    device_ext: str
    version: str
    technique: str
    interval_s: float
    base_time: datetime
    recipe_log_path: str = ""
    curves: List[DropSensCurve] = field(default_factory=list)


def read_pad_measurement(path: str) -> DropSensMeasurement:
    """Read a DropSens PAD .mtp file."""
    measurement = read_dropsens_measurement(path)
    if measurement.technique.upper() != "PAD":
        raise MeasurementDataError(
            f"Only PAD DropSens files are supported for now: {path}"
        )
    if not measurement.curves:
        raise MeasurementDataError(f"No curves found in DropSens file: {path}")
    return measurement


def read_cv_measurement(path: str) -> DropSensMeasurement:
    """Read a DropSens CV .mtc file."""
    measurement = read_dropsens_measurement(path)
    if measurement.technique.upper() != "CV":
        raise MeasurementDataError(
            f"Only CV DropSens files are supported for CV view: {path}"
        )
    if not measurement.curves:
        raise MeasurementDataError(f"No curves found in DropSens file: {path}")
    return measurement


def read_pad_measurement_series(path: str) -> tuple[List[float], List[float]]:
    """Return PAD data as ``(unix_timestamps, current_uA)`` lists."""
    measurement = read_pad_measurement(path)
    curve = measurement.curves[0]
    times = curve.points.get("time", [])
    currents = curve.points.get("i1", [])

    total = min(len(times), len(currents))
    if total <= 0:
        raise MeasurementDataError(f"No PAD time/current data found: {path}")

    base_time = measurement.base_time
    timestamps = [
        (base_time + timedelta(seconds=times[idx])).timestamp()
        for idx in range(total)
    ]
    return timestamps, currents[:total]


def read_dropsens_measurement(path: str) -> DropSensMeasurement:
    """Read common DropSens XML metadata and curve point arrays."""
    if not os.path.isfile(path):
        raise MeasurementDataError(f"DropSens file not found:\n{path}")

    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        raise MeasurementDataError(f"Failed to read DropSens file: {path}\n{exc}")

    root = tree.getroot()
    file_node = root.find("file")
    filename_title = os.path.splitext(os.path.basename(path))[0]
    title = _text(root.find("title")) or filename_title
    curves: List[DropSensCurve] = []

    for curve_node in root.findall("./curves/curve"):
        curves.append(_parse_curve(curve_node))

    technique = curves[0].technique if curves else ""
    interval_s = _safe_float(_text(root.find("./curves/curve/technic/interval")))
    recipe_log_path = _find_companion_recipe_log(path)
    base_time = (
        _select_recipe_start_time(recipe_log_path, path, curves)
        or get_file_time(path)
    )

    return DropSensMeasurement(
        path=path,
        title=title,
        part_number=_derive_part_number(title, path),
        device=file_node.get("device", "") if file_node is not None else "",
        device_ext=file_node.get("device_ext", "") if file_node is not None else "",
        version=file_node.get("version", "") if file_node is not None else "",
        technique=technique,
        interval_s=interval_s,
        base_time=base_time,
        recipe_log_path=recipe_log_path,
        curves=curves,
    )


def _parse_curve(curve_node: ET.Element) -> DropSensCurve:
    technic = curve_node.find("technic")
    technique = technic.get("id", "") if technic is not None else ""
    return DropSensCurve(
        name=_text(curve_node.find("name")),
        title=_text(curve_node.find("title")),
        technique=technique,
        parameters=_parse_parameter_groups(curve_node),
        points=_parse_points(curve_node.find("points")),
        overload=_parse_bool_list(_text(curve_node.find("./points/overload"))),
    )


def _parse_parameter_groups(curve_node: ET.Element) -> Dict[str, Dict[str, str]]:
    groups = {
        "pretreatment": "./technic/pretreatmentUserParameters/parameter",
        "common": "./technic/commonUserParameters/parameter",
        "channel": "./technic/channelUserParameters/channel/parameter",
    }
    result: Dict[str, Dict[str, str]] = {}
    for group_name, selector in groups.items():
        params: Dict[str, str] = {}
        for node in curve_node.findall(selector):
            param_id = node.get("id", "")
            if param_id:
                params[param_id] = _text(node)
        result[group_name] = params
    return result


def _parse_points(points_node: ET.Element | None) -> Dict[str, List[float]]:
    if points_node is None:
        return {}
    points: Dict[str, List[float]] = {}
    for name in ("time", "potential", "i1", "i2", "ecl"):
        points[name] = _parse_float_list(_text(points_node.find(name)))
    return points


def _parse_float_list(text: str) -> List[float]:
    values: List[float] = []
    for raw in (text or "").replace("\r", "").replace("\n", "").split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            values.append(float(item))
        except ValueError:
            continue
    return values


def _parse_bool_list(text: str) -> List[bool]:
    values: List[bool] = []
    for raw in (text or "").replace("\r", "").replace("\n", "").split(","):
        item = raw.strip().lower()
        if item:
            values.append(item == "true")
    return values


def _derive_part_number(title: str, path: str) -> str:
    filename_title = os.path.splitext(os.path.basename(path))[0]
    candidates = [filename_title] if _is_placeholder_title(title) else [title, filename_title]
    for candidate in candidates:
        match = re.match(r"(.+?)-PAD(?:-| |$)", candidate, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    parent = os.path.basename(os.path.dirname(path))
    return parent or "DropSens PAD"


def _is_placeholder_title(title: str) -> bool:
    return bool(re.match(r"^0+-0+-0+-PAD", title or "", flags=re.IGNORECASE))


def _find_companion_recipe_log(path: str) -> str:
    base, _ext = os.path.splitext(path)
    candidates = [base + ".txt"]

    dash_index = base.rfind("-")
    if dash_index >= 0:
        candidates.append(base[:dash_index] + " " + base[dash_index + 1:] + ".txt")

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    folder = os.path.dirname(path)
    wanted = _normalize_companion_name(os.path.basename(base))
    try:
        for name in os.listdir(folder):
            txt_path = os.path.join(folder, name)
            if not os.path.isfile(txt_path):
                continue
            txt_base, txt_ext = os.path.splitext(name)
            if txt_ext.lower() != ".txt":
                continue
            if _normalize_companion_name(txt_base) == wanted:
                return txt_path
    except OSError:
        return ""

    return ""


def _normalize_companion_name(name: str) -> str:
    return re.sub(r"[\s_-]+", "", name).lower()


def _select_recipe_start_time(
    log_path: str,
    measurement_path: str,
    curves: List[DropSensCurve],
) -> datetime | None:
    if not log_path:
        return None
    result = LogParser().parse(log_path)
    starts = [event.timestamp for event in result.system_events if event.event_type == "started"]
    if not starts:
        return None
    if len(starts) == 1:
        return starts[0]

    estimated_start = _estimate_start_from_file_time(measurement_path, curves)
    if estimated_start is None:
        return starts[-1]
    return min(starts, key=lambda item: abs((item - estimated_start).total_seconds()))


def _estimate_start_from_file_time(
    path: str,
    curves: List[DropSensCurve],
) -> datetime | None:
    if not curves:
        return None
    times = curves[0].points.get("time", [])
    if not times:
        return None
    try:
        file_time = datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return None
    return file_time - timedelta(seconds=times[-1])


def _safe_float(text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()
