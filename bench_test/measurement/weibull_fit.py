"""DPAD step fits, independent of Qt, plot clipping, and display zoom.

Inputs are the lower graph's series after its fixed downsampling operation.
Currents and the Cauchy residual scale are in nA; times are Unix seconds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math

import numpy as np


MODEL_VERSION = "weibull-cauchy-nonpositive-drift-v2"


@dataclass(frozen=True)
class FitOptions:
    delay_seconds: float = 630.0
    min_points: int = 10
    tolerance_na: float = 4.0
    rise_shape: float = 1.3524384687751219
    fall_shape: float = 1.0291958303419821

    def validate(self):
        if not math.isfinite(self.delay_seconds) or self.delay_seconds < 0:
            raise ValueError("Response Delay must be a finite nonnegative value.")
        if self.min_points < 3:
            raise ValueError("Weibull needs at least 3 points per transition.")
        if not math.isfinite(self.tolerance_na) or self.tolerance_na <= 0:
            raise ValueError("Outlier tolerance must be positive (nA).")
        if min(self.rise_shape, self.fall_shape) <= 0:
            raise ValueError("Weibull shapes must be positive.")


@dataclass(frozen=True)
class ValveState:
    time: float
    port: int
    position: str
    source: str = ""


@dataclass
class FitResult:
    x: np.ndarray = field(default_factory=lambda: np.empty(0))
    y: np.ndarray = field(default_factory=lambda: np.empty(0))
    status: str = "Yetersiz veri."
    cache: dict = field(default_factory=dict)
    fits_computed: int = 0


def valve_states(events, cutoff):
    """Collapse repeated acknowledgements, retaining independent log sources."""
    states, current = [], {}
    for event in sorted(events, key=lambda item: item.timestamp):
        stamp = event.timestamp.timestamp()
        if stamp > cutoff:
            continue
        source = event.source
        old = current.get(source, (None, None))
        state = (event.port_a if event.port_a is not None else old[0],
                 event.valve_b if event.valve_b is not None else old[1])
        current[source] = state
        if state != old and None not in state:
            states.append(ValveState(stamp, state[0], state[1], source))
    return states


def a_port_states(events, cutoff):
    """Fit edges follow A ports; B-mode changes continue the current phase."""
    selected = []
    for state in valve_states(events, cutoff):
        if not selected or state.source != selected[-1].source or state.port != selected[-1].port:
            selected.append(state)
    return selected


def prepare_series(x, y, ds=1, method="subsample", offset=0):
    """Apply the lower graph's fixed display transform over its full series."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.shape != y.shape or x.ndim != 1:
        raise ValueError("Weibull input time/current arrays must have equal length.")
    ds = max(1, int(ds))
    if ds > 1:
        if method == "subsample":
            start = max(0, int(offset)) % ds
            x, y = x[start::ds], y[start::ds]
        else:
            n = len(x) // ds
            centers = x[ds//2:ds//2+n*ds:ds]
            blocks = y[:n*ds].reshape(n, ds)
            if method == "mean":
                x, y = centers, blocks.mean(axis=1)
            elif method == "peak":
                x = np.repeat(centers, 2)
                y = np.column_stack((blocks.max(axis=1), blocks.min(axis=1))).ravel()
            else:
                raise ValueError(f"Unsupported downsampling method: {method}")
    valid = np.isfinite(x) & np.isfinite(y)
    order = np.argsort(x[valid], kind="stable")
    return x[valid][order], y[valid][order]


def _evaluate_model(x, starts, levels, durations, slopes, options, with_jacobian=False):
    """Time in minutes; slopes in nA/hour. Parameter order: levels, T95, slopes."""
    x = np.asarray(x, dtype=float)
    phases = len(starts)+1
    drift_offset = 2*phases-1
    result = np.empty(len(x), dtype=float)
    jacobian = np.zeros((len(x), 3*phases-1)) if with_jacobian else None
    before = float(levels[0])
    before_jac = np.zeros(3*phases-1) if with_jacobian else None
    for j in range(phases):
        begin = 0. if j == 0 else starts[j-1]
        end = starts[j] if j < len(starts) else np.inf
        active = x < end if j == 0 else (x >= begin) & (x < end)
        hours = (x[active]-begin)/60
        if j == 0:
            result[active] = levels[0]+slopes[0]*hours
            if with_jacobian:
                jacobian[active, 0] = 1
                jacobian[active, drift_offset] = hours
                before_jac[0] = 1
            if np.isfinite(end):
                before = levels[0]+slopes[0]*end/60
                if with_jacobian:
                    before_jac[drift_offset] = end/60
            continue
        duration = durations[j-1]
        shape = options.rise_shape if levels[j] >= before else options.fall_shape
        power = np.log(20)*np.maximum((x[active]-begin)/duration, 0)**shape
        remain = np.exp(-power)
        fraction = 1-remain
        result[active] = remain*before+fraction*levels[j]+slopes[j]*hours
        if with_jacobian:
            jacobian[active] = remain[:, None]*before_jac
            jacobian[active, j] += fraction
            jacobian[active, phases+j-1] += (levels[j]-before)*(-remain*shape*power/duration)
            jacobian[active, drift_offset+j] += hours
        if np.isfinite(end):
            power_end = np.log(20)*((end-begin)/duration)**shape
            remain_end = float(np.exp(-power_end))
            next_before = remain_end*before+(1-remain_end)*levels[j]+slopes[j]*(end-begin)/60
            if with_jacobian:
                before_jac *= remain_end
                before_jac[j] += 1-remain_end
                before_jac[phases+j-1] += (levels[j]-before)*(-remain_end*shape*power_end/duration)
                before_jac[drift_offset+j] += (end-begin)/60
            before = next_before
    return (result, jacobian) if with_jacobian else result


def evaluate(x, starts, levels, durations, options, slopes=None):
    """Continue from the preceding phase's full value, including its drift.

    Optional zero slopes preserve the pure Weibull kernel for callers. The fit
    below always estimates one nonpositive drift per A-port phase, without a toggle.
    """
    if slopes is None:
        slopes = np.zeros(len(levels))
    return _evaluate_model(x, starts, levels, durations, slopes, options)


def _session_blocks(states, delay, last):
    blocks, active = [], None
    for state in states:
        arrival = state.time+delay
        if active and active[0].source != state.source:
            blocks.append((active, arrival))
            active = None
        if active is None:
            active = [state]
        else:
            active.append(state)
    if active:
        blocks.append((active, last+1))
    return blocks


def _fit_block(times, values, events, options, previous):
    from scipy.optimize import least_squares

    origin = times[0]
    x = (times-origin)/60
    starts = np.array([(s.time+options.delay_seconds-origin)/60 for s in events])
    n = len(starts)
    labels = np.searchsorted(starts, x, side="right")
    counts = [len(np.unique(times[labels == j])) for j in range(n+1)]
    # Do not estimate a newly arriving edge from one or two points.
    keep = n
    while keep and counts[keep] < 3:
        keep -= 1
    usable = labels <= keep
    x, y = x[usable], values[usable]
    starts = starts[:keep]
    events = events[:keep]
    counts = counts[:keep+1]
    n = keep
    if len(x) < 3:
        return None
    span = max(float(np.ptp(y)), options.tolerance_na*10, 1.0)
    drift_offset = 2*n+1
    lo = np.r_[np.full(n+1, y.min()-10*span), np.ones(n), np.full(n+1, -5*span)]
    hi = np.r_[np.full(n+1, y.max()+10*span), np.full(n, 60.), np.zeros(n+1)]
    levels = []
    for j in range(n+1):
        phase = y[np.searchsorted(starts, x, side="right") == j]
        levels.append(float(np.mean(phase[-5:])) if len(phase) else float(y[0]))

    memory = {}
    def compute(p):
        if "parameters" not in memory or not np.array_equal(p, memory["parameters"]):
            curve, jacobian = _evaluate_model(x, starts, p[:n+1], p[n+1:drift_offset],
                p[drift_offset:], options, with_jacobian=True)
            memory.update(parameters=p.copy(), curve=curve, jacobian=jacobian)

    def residual(p):
        compute(p)
        return (memory["curve"]-y)/options.tolerance_na

    def jacobian(p):
        compute(p)
        # SciPy rescales robust Jacobians in-place; retain an unmodified cache.
        return memory["jacobian"].copy()/options.tolerance_na

    guesses = [np.r_[levels, np.full(n, duration), np.zeros(n+1)] for duration in (5., 15.)]
    drift_levels, drift_slopes = np.array(levels), np.zeros(n+1)
    phase_labels = np.searchsorted(starts, x, side="right")
    for j in range(n+1):
        begin = 0. if j == 0 else starts[j-1]
        late = (phase_labels == j) & (x >= begin+20)
        if np.sum(late) >= 5:
            # Initialization only. The optimization still uses every sample.
            design = np.column_stack(((x[late]-begin)/60, np.ones(np.sum(late))))
            drift_slopes[j], drift_levels[j] = np.linalg.lstsq(design, y[late], rcond=None)[0]
    guesses.append(np.r_[drift_levels, np.full(n, 15.), drift_slopes])
    keys = [s.time for s in events]
    if previous and previous.get("edges") == keys:
        guesses.insert(0, np.r_[previous["levels"], previous["durations"], previous["slopes"]])
    attempts = [least_squares(residual, np.clip(p, lo+1e-7, hi-1e-7),
        jac=jacobian, bounds=(lo, hi), loss="cauchy", f_scale=1., x_scale="jac", max_nfev=500,
        ftol=1e-7, xtol=1e-7, gtol=1e-7) for p in guesses]
    successful = [r for r in attempts if r.success]
    if not successful:
        raise RuntimeError("Weibull hesabı yakınsamadı; önceki sonuç korundu.")
    best = min(successful, key=lambda r: r.cost)
    levels, durations = best.x[:n+1], best.x[n+1:drift_offset]
    slopes = best.x[drift_offset:]
    gx, gy = [], []
    # Separate hidden/new phases and discontinuous blocks with NaN, never bridge.
    for j, count in enumerate(counts):
        begin = 0. if j == 0 else starts[j-1]
        # A short finished phase can be drawn retrospectively once enough
        # observations exist after its onset. Do not leave permanent holes at
        # short A-port intervals; retain the live threshold at the frontier.
        if count < 3 or len(np.unique(x[x >= begin])) < options.min_points:
            continue
        end = min(x[-1], starts[j] if j < n else x[-1])
        if end < begin:
            continue
        grid = np.linspace(begin, end, max(2, int((end-begin)*4)+1))
        gx.extend((origin+grid*60).tolist()+[np.nan])
        gy.extend(evaluate(grid, starts, levels, durations, options, slopes).tolist()+[np.nan])
    return dict(x=np.asarray(gx), y=np.asarray(gy), edges=keys,
                levels=levels.tolist(), durations=durations.tolist(), slopes=slopes.tolist(),
                model=MODEL_VERSION,
                latest_points=counts[-1], phases=n,
                at_bound=bool(len(durations) and (durations[-1] > 59.9 or durations[-1] < 1.01)))


def fit_steps(times, currents_na, events, options=FitOptions(), cache=None):
    options.validate()
    result = FitResult(cache={})
    x, y = prepare_series(times, currents_na)
    if len(x) < 3:
        return result
    states = a_port_states(events, x[-1])
    if not states:
        result.status = "Gerçek A/B valf durumlarını içeren uygun log bulunamadı."
        return result
    previous_cache = cache or {}
    unique = np.unique(x)
    gap = max(180., 3*float(np.median(np.diff(unique)))) if len(unique) > 1 else 180.
    output_x, output_y, messages = [], [], []
    latest_count = 0
    phases = 0
    for states_in_block, end in _session_blocks(states, options.delay_seconds, x[-1]):
        start = states_in_block[0].time+options.delay_seconds
        select = (x >= start) & (x < end)
        bx, by = x[select], y[select]
        splits = np.r_[0, np.flatnonzero(np.diff(bx) > gap)+1, len(bx)]
        for left, right in zip(splits, splits[1:]):
            tx, ty = bx[left:right], by[left:right]
            if len(tx) < 3:
                continue
            edges = [s for s in states_in_block[1:] if tx[0] < s.time+options.delay_seconds <= tx[-1]]
            key = (states_in_block[0].source, float(tx[0]), options, MODEL_VERSION)
            digest = hashlib.blake2b(tx.tobytes()+ty.tobytes()+repr(edges).encode(), digest_size=16).hexdigest()
            previous = previous_cache.get(key)
            try:
                if previous and previous["digest"] == digest:
                    fitted = previous["fit"]
                else:
                    fitted = _fit_block(tx, ty, edges, options, previous["fit"] if previous else None)
                    result.fits_computed += 1
            except RuntimeError as exc:
                messages.append(str(exc))
                fitted = previous["fit"] if previous else None
                digest = previous["digest"] if previous else digest
            if fitted is None:
                continue
            result.cache[key] = dict(digest=digest, fit=fitted)
            output_x.extend(fitted["x"])
            output_y.extend(fitted["y"])
            phases += fitted["phases"]
            latest_count = fitted["latest_points"]
            last_arrivals = [s.time+options.delay_seconds for s in states_in_block[1:] if s.time+options.delay_seconds <= tx[-1]]
            if last_arrivals:
                latest_count = len(np.unique(tx[tx >= last_arrivals[-1]]))
            if fitted["at_bound"]:
                messages.append("Geçiş süresi sınırda; bu bölüm geçici tahmindir.")
    result.x, result.y = np.asarray(output_x), np.asarray(output_y)
    if messages:
        result.status = " ".join(dict.fromkeys(messages))
    elif latest_count < options.min_points:
        result.status = f"Yeni bölüm için veri bekleniyor: {latest_count}/{options.min_points} nokta."
    else:
        result.status = f"Weibull–Cauchy + doğrusal drift hazır · {phases} geçiş."
    return result
