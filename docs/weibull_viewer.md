# Robust Weibull overlay (DPAD)

Select **Plot Options > Average > Method > Weibull - Robust** on either plot.
The method and fit parameters are shared between both plots. Average can then
be unchecked independently to hide either overlay. Switching back restores the
existing Moving Average / Savitzky-Golay path.

The input is the lower graph's complete series, followed by its selected fixed
Subsample, Mean or Peak operation (including Subsample Start). The input is not
restricted to the visible range. Zoom and Clip to View do not refit the data.
Explicitly changing lower-plot downsampling does refit it. If Auto downsampling
is enabled, an explanation asks for a fixed step: a zoom-dependent input cannot
also produce a zoom-independent fit. No setting is silently changed. All
original plotted points remain available.

## Parameters

- **Response Delay** uses the existing viewer minutes/seconds controls. Actual
  A-port change times receive this offset exactly once.
- **Min points** defaults to 10 distinct lower-graph timestamps per phase.
  Duplicate timestamps from Peak do not count twice. This is a drawing threshold,
  not a settling or accuracy guarantee. Numerical fitting starts at three points.
  Short completed phases with at least three own observations may appear once
  ten observations have accumulated since their onset, including later phases.
  This avoids permanent holes while retaining the live drawing threshold.
- **Outlier tolerance** defaults to 4 nA. It is the Cauchy residual scale around
  the fitted curve, never an absolute current cutoff. A real 15 nA plateau is
  valid, just as a real 150 nA plateau is. Points are not deleted.

The two fit settings are remembered in viewer preferences. Numerical input is
converted from plot amperes to nA and the result back to amperes. Visible XLSX
export includes the common curve, method, delay, tolerance and shape profile.
It also identifies the always-on linear drift model, its nA/hour units, and the
`slope<=0` constraint.

## Model and updates

Linear drift is always part of the model; there is no extra checkbox or user
parameter. Each A-port phase has a fitted reference target, T95 and nonpositive
slope in nA/hour. A flat phase can have a slope near zero; positive drift is not
allowed. The same slope continues through Load and Inject without restarting.
The Weibull step itself can still rise when the A-port input rises.

For elapsed time u (minutes), a phase is modeled as
`I(u) = I0 + (L - I0) * F_Weibull(u / T95) + slope * u / 60`.
`L` is referenced to the phase start, not a constant infinite-time plateau.
T95 describes the Weibull step component, not settling of the whole drifting
signal. The first observed phase uses its own fitted level and linear slope.
The next A phase starts from the previous phase's full value, including drift.

The fixed rising and
falling Weibull exponents are 1.3524384688 and 1.0291958303, transferred from the
earlier bench trials. Transition direction follows the fitted levels, not the
numeric valve port. T95 is bounded to 1–60 minutes; reaching a bound is reported.
Interrupted transitions remain continuous. There is no 120-minute history cap.
The curve ends at the last usable measurement, never in the unmeasured future.
New data jointly updates the reference levels, transition times and slopes.

Actual `Valve A: At port ...` and `Valve B: Load/Inject ...` acknowledgements are
read, but only an A-port change starts a new fitted phase. A B-only Load/Inject
change continues the existing phase, adding no target, T95 or slope and not
resetting the drawing threshold. B events remain in the log and existing graph
markers. Duplicate states collapse; recipe announcements do not create
transitions. Separate session logs retain their own initial states. Missing
actual A/B states produce an explanation rather than a substitute smoothing line.

Both Load and Inject data contribute to the existing A-port phase. An A-port
change during Inject still starts a new delayed transition. Separate session logs
and significant acquisition gaps remain separate blocks; missing data is not
bridged by a line. Finished blocks are cached; adding
new data only refits changed blocks. A later change of fit parameters invalidates
the corresponding cache. Each new fit can revise earlier targets within the
same continuous block. No future measurements or future valve events are used.

One worker runs outside the UI thread. Requests are coalesced; responses from
an older session, setting or input snapshot cannot overwrite a newer view.
The acquisition, recipe and watchdog code paths are unchanged.

## Installation and validation

Install `requirements-weibull.txt` into the BTC environment before starting a
source run or creating a release. SciPy is imported lazily inside the fit worker;
if missing, the viewer reports it and the other smoothing methods remain usable.
The release build explicitly includes SciPy's dynamically imported
`scipy._external.array_api_compat.numpy.fft` and its common FFT helper.
Standard SciPy hooks alone miss this import with SciPy 1.18. Import failures
report the original exception and write its traceback to the application log;
frozen builds recommend rebuilding the package rather than installing into Python.
Release packaging should be verified before distributing a new executable.

Run the hardware-free numerical and offscreen Qt integration tests:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tools -p 'test_weibull*.py' -v
```

Short phases can confound drift with the reference level and T95; an early fit
is provisional. The profile and 4 nA scale require further validation across sensors and noise
levels. A sustained unexpected change is model mismatch or a measurement issue,
not proof that all its points are faulty. Inspect the preserved raw points.
