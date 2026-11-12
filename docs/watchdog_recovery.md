# Discrete PAD watchdog: implementation phases

## Phase 1 — durable execution records (implemented)

Discrete PAD starts create `logs/recipe_recovery.json` in the session in both
normal and simulation mode.
The session file registry contains its path under `recipe_recovery`.
Continuous PAD and CV skip the record.

The record stores the recipe, live edits, the copy of the step currently being
executed, the outer loop and expanded inner-loop position, operation phase,
measurement state, remaining wait time and connection settings. Wait updates
are written approximately every five seconds; operation boundaries, live edits,
pause/resume and user stop requests are recorded immediately. A blocking device
operation has its last boundary recorded, not an independent heartbeat.

Writes use a temporary file in the same directory, flush + fsync, then replace.
A failed write leaves the previous complete record intact and reports that
recovery recording is unavailable. No hardware command is added to recording.
The saved `requested_settings` are command intentions, **not measured physical
valve or pump positions**. Unknown initial values remain null. Device readback
and reconciliation belong to the recovery executor in phase 2.

`plan_recovery()` only computes a cursor and has no device/UI effects:

- An interrupted Discrete PAD wait resumes the same sequence item with its
  saved remaining duration, including the final step of the final loop.
  Missing or invalid remaining durations block recovery.
- Start DropView, Exit DropView and measurement stop actions are never skipped
  merely because their step contains a wait.
- Unfinished device operations remain at the same step. Replaying their device
  commands is **not** authorized by a cursor alone; phase 2 must reconcile them.
- A completed or disabled step advances exactly once.
- Outer-loop boundaries and expanded inner loops retain their position.
- A completed last step of the final loop produces `finished`.
- A user stop, normal finish, pause, unsupported mode or invalid record is
  rejected as an automatic recovery candidate.

The active step is stored separately from the mutable recipe because live
changes take effect at its next arrival. A mid-step live edit must not redefine
the operation already in progress.

Phase 2 adds an explicit recovery entry; phase 3 adds supervision below. Normal recipe
startup still has its original pump stop; it must not be reused as a recovery
startup path. If a recording warning occurs, an old snapshot must not be treated
as proof of current device state.

Validation (no hardware/UI operation):

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tools -p test_*.py
```

## Phase 2 — recovery executor (implemented; hardware validation pending)

Recipe Control has a **Recover Discrete PAD** button. Select the interrupted
session's `logs/recipe_recovery.json`. `RecipeTab.recover_from_record(path)` is
also callable by the supervisor without confirmation dialogs.

The executor creates a uniquely named session beside the old one, copies the
saved edited method byte-for-byte, retains the script's repeat/wait settings,
repoints LOADMETHOD and every EXPORTCURVES path, and writes the live recipe.
`logs/recovery_origin.json` links both packages and records the recovery cursor.
The old method, script and measurements are not overwritten. The old recovery
record is marked `recovering`, then `superseded` only after successful setup.
Failures during setup leave that source eligible for a new explicit attempt.
`recovery_resume_basis` records why continuation was allowed, including verified
editor idle, verified original process exit, or reconnection after confirmed
link loss. `recovery_connection_restored` separately records whether the
current recovery attempt re-established the connection. Older records with
`recovery_previous_stopped` are still readable.
If recovery itself is interrupted before the new cursor becomes runnable, the
supervisor can return to the verified parent record. The stored package links
must match and a user Stop or Pause is checked before any record is reclaimed.
Once the child cursor is runnable, it takes precedence. A crash after the
intent to Start a replacement measurement was saved, but before Start was
confirmed, requires verifying that measurement's stop before trying Start again.

Valve reconnection uses saved serial settings and position queries, never reset
or home. A different position blocks recovery unless that exact movement was
interrupted and still needs its target applied. A target already reached is not
commanded again. Settings applied before an interrupted operation are not
replayed. Both control and measurement waits retain their recorded remaining
duration. Recovery restarts an interrupted measurement in the new package,
including a waiting Start Discrete PAD step. The remaining-duration countdown
starts after the first new CSV is verified. Recovery and verification time do
not consume the saved wait. Live edits apply on the next visit to the step.

An inherited measurement is stopped and verified before a fresh measurement is
started in the new package, including when the next step has action `none`.
If reconciliation fails, recovery can reconnect or relaunch DropView. Generic
recovery does not force termination while the previous measurement may still
be active. Before terminating a process it checks the PID and process creation
time. Reopening DropView or seeing a new idle editor is not proof that the old measurement
stopped. If the old measurement may still be active and its stop cannot be
verified, recovery reports `operator_required` and does not issue Start.

A confirmed interruption holds a recipe-controlled Flexicon pump using ST and
requires a fresh ZY=0 reply. If the application crashed, the recovery runner
reconnects and verifies pump stop before reconciling DropView or moving valves.
The recorded recipe RPM is preserved separately. At the continuation step,
an explicit RPM overrides the saved RPM; a blank field uses the known inherited
RPM and leaves the pump stopped if it is unknown. Zero stays stopped. No wash or
valve homing is added. Arduino serial reopen remains blocked. Normal completion
and user Stop retain their existing behavior.

`QLockFile` guards recipe execution per installation, including recovery, with
stale PID handling and no timeout-based lock stealing. A source revision check
rejects a changed/superseded record before any device commands. Recovery
packages are kept automatically; completion/errors are logged without blocking
archive or result dialogs. Journal-backed PAD packages are retained automatically
and monitored runs log results without blocking result dialogs.

Tests include the generated application script format, fake device readback and
failure paths, lock exclusion, and an offscreen Qt recovery-entry integration.
No test opens a serial port or operates DropView. Real-device validation is still
required before unattended use.

## Release pairing

`build_release.ps1 VERSION` produces `BenchTestController_vVERSION.exe` and
`BenchTestWatchdog_vVERSION.exe`. Keep both files from the same build in the
same directory. Use a new version number for each distributed release.
Packaged BTC requires the matching versioned watchdog; it does not use an old
unversioned watchdog or fall back to its embedded supervisor when WD is missing.
Before supervision starts, BTC probes WD's embedded `APP_VERSION` with a
15-second timeout in a background thread. A missing, incompatible or unresponsive
WD is reported through the watchdog status and log. Renaming a different version
does not satisfy the probe. WD also checks `--app-version` at normal startup.
Source runs retain the `main.py --watchdog` path.

CSV content checks are shared through `bench_test.csv_progress`, which uses only
the Python standard library. The watchdog imports this module directly, avoiding
the measurement package's Excel and GUI dependencies. The former
`bench_test.measurement.data_io` import remains available for BTC callers and
refers to the same checker. QtCore remains required for the existing QLockFile
process locks; their behavior is unchanged.

## Phase 3 — independent supervision (implemented; hardware validation pending)

Journal-backed Discrete PAD recipes automatically arm `WatchdogClient` in both
modes. At recipe start BTC lists any disconnected pump or valve and asks once
whether to continue in simulation. If accepted, BTC skips all pump and valve
commands and checks. DropView and DropSens use the same controls and recovery
rules as normal mode. The saved mode is inherited by the recovery package.
Test sessions are saved under `_watchdog_simulation` in the selected package
root. Watchdog state uses a separate `simulation` subdirectory. Simulation
leaves Windows Update policy alone. Continuous PAD and CV are
outside this watchdog scope. The client
starts the matching standalone WD for packaged releases, or the same interpreter
with `--watchdog` for source runs; this separate process
observes JSON state and requests cooperative interruption. It never sends device
commands. If the owner remains hung, it checks the latest recoverable record and
PID creation time before terminating that application, then starts
`--watchdog-recover TOKEN`. Other applications and DropView are not terminated by
the supervisor. Device reconciliation belongs to the recovery executor.
On startup a token must still match the armed run and the previous application's
process identity. A newer run, Stop, Pause or cancellation rejects an old token
before reclaiming the saved recipe.

Runtime files live under `%LOCALAPPDATA%\BenchTestController\watchdog\<install hash>`:
`active.json` is written by the UI; `command.json`, `supervisor_status.json`,
`supervisor_ledger.json` and rotating `watchdog.log` belong to the monitor.
`update_policy.json` tracks Windows policy ownership. Separate application and
supervisor locks prevent duplicates. Retry timestamps are rebased after reboot;
retry counts survive supervisor restarts. Preserve these files for diagnosis.

Detection defaults:

| Observation | Limit / response |
| --- | --- |
| UI heartbeat | 60 seconds |
| Worker in measurement wait | 45 seconds |
| Valve / pump operation | 90 seconds |
| DropView start / measurement start-stop / exit | 600 seconds |
| Recovery reconciliation / blocked final aggregation | 900 seconds |
| Fresh missing, unresponsive or disconnected DropView | 15 seconds with a fresh confirming observation |
| Tracked DropView process confirmed exited | No additional confirmation delay |
| Multiscript missing while a measurement is expected | 3 consecutive probes; hidden windows do not count |
| Cooperative interruption | 60 seconds; recovery startup 180 seconds |
| Consecutive recovery attempts | At most 3; 30/60-second retry backoff |
| Retry budget reset | 5 minutes of healthy connected measurement wait |

Prewarnings do not trigger recovery: the watchdog reports two distinct missing
window observations or three distinct disconnected/unresponsive observations
before the 15-second confirmation. It also warns after 30 seconds
without a definitive connection read, after 30 seconds without a UI update,
or after two failed directory scans. Repeated watchdog ticks do not count as
new observations or repeat an unchanged warning in the log.

The status bar shows the last definitive connection read, successful CSV scan
and valid content progress. A confirmed fault is reported separately.
Recovery success is logged once the new recipe reaches an actual wait or
completes a step, after recovery preparation and any required measurement
restart, not when the recovery worker is merely launched.

Planned DropView closures are not connection faults. Covered/minimized windows,
unknown image matches and stale health samples are reported as unverified rather
than treated as disconnected. A single background CSV probe observes file
metadata each minute. For the standard packaged Discrete PAD script
`LOADMETHOD → REPEAT (RUN → EXPORTCURVES → WAIT)`, it reads the PAD measurement
and pretreatment durations from the packaged method. The first export is due
after those durations; the next export interval also includes the script WAIT.
A tolerance of 60 seconds or 25% of the applicable duration, whichever is
larger, is added. A late first export warns; a second successful scan with no
export requests recovery. Later exports warn after one missed interval and
request recovery after two missed intervals and a separate successful scan.
Files from before this measurement, empty files, and files with unrelated
export names do not count. A complete set of numbered exports does not stall.

If the package timing cannot be determined, the fallback learns at least two
write intervals. A gap exceeding both 20 minutes and five times the longest
learned interval requests recovery; without a learned cadence, 30 minutes
without sufficient data produces a warning. Storage errors and stale scans
produce warnings and do not confirm a missed export. Recovery deadlines still
use file activity, not the CSV content checks below.

The same background scan checks the latest matching DropView CSV, up to 256 KiB,
for finite time/current values and increasing time within the curve. Each new
curve may start at zero and repeat the same readings. An advancing curve number
or more time samples on the same curve is content progress. If the files keep
changing but records do not advance for two export intervals, the watchdog
warns. It also warns after two scans with invalid or unverifiable content.
Unsupported layouts, oversized files and files that change during the read
are unverified. These warnings do not trigger recovery or judge the
electrochemical quality of the data.

During a Discrete PAD wait, the recipe worker reads the original DropView status
row every three seconds without activating the window. `Stopped` can be normal
between exports. After 60 seconds without a new `Run` or recent verified CSV
progress, the worker checks Multiscript. Three consecutive unambiguous green
observations interrupt recipe progression. For a reopened editor, the green
observations also require a stopped status and verified connection in the
original DropView session, with no new CSV progress during verification.
The first editor read is passive. If the window is covered or minimized, BTC
can try to reveal it and read again. A visible Stop or Exit button can be
clicked after verifying the target coordinates belong to the selected editor.
Keyboard shortcuts still require the target to have foreground focus. Planned
Discrete PAD Stop can reopen the editor in the original session and confirms
idle in that editor without waiting for a new CSV export. A journal-backed run requests the usual
recovery flow in both normal and simulation mode.
Captures are serialized and reject covered indicators. Active connection polling
tries a passive read first and may activate DropView if its status region is covered.
Idle and paused connection polling stays passive. A still covered, missing or
ambiguous editor remains unverified. A readable indicator is usable even if the
foreground request fails. This cannot bypass the Windows lock screen.
CSV observation remains the slower fallback. An
already idle editor is verified and closed without clicking its disabled Stop
button during recovery. Actual indicator matching still requires a DropView
test on the intended machine.

User Stop/close first signals a named Windows event, fencing pending launches
even if writing stop intent to disk blocks. Pause, terminal journals and changed
process owners forbid automatic recovery. A journal failure disables recovery
and reports it. The update pause is retained while the physical experiment may
still be running. Recovery waits for the old worker and package aggregation to
finish before adopting a new package.

Normal startup and recovery share the same DropSens connection sequence. An
explicitly selected port takes priority, followed by the last successful manual
port and the other currently present supported ports (COM3/COM4/COM10). Ctrl+C
is the final fallback. Its actual USB/Bluetooth transport is not independently
queried. A successful automatic connection preserves the last successful COM.

After a verified DropView process exit during Discrete PAD, recovery rechecks
the saved process identity, uses normal DropView startup and waits for a
confirmed DropSens connection. It loads a new script when the target step
requires measurement. After a confirmed DropSens disconnection, recovery first
reconnects in the original DropView process if it is still running, then uses the
same continuation logic. Missing or unresponsive windows use the existing
recovery checks.

For a confirmed DropView hang, recovery first tries to reconcile the original
editor. If it still cannot control the measurement, it verifies the recorded
DropView process (including the bundled Java runtime), requests a normal close,
and escalates to process termination only if the same window remains
unresponsive. A user Stop cancels escalation and relaunch. After confirmed
exit, recovery uses the normal reconnect and continuation sequence.

### Windows Update boundaries

The guard requests the documented quality/feature pause policies, for at least
15 days or recipe duration plus one day. Windows limits this pause to 35 days.
It checks pending reboot and supported editions, backs up ownership before any
write, preserves pre-existing policies and restores only values still matching
its own changes. Partial writes are rolled back. It never disables services or
tasks and never invokes elevation automatically. Insufficient permissions,
external policy, unsupported edition/duration and pending reboot remain visible.

A policy request alone is not displayed as verified protection. Both effective
Windows pause states and matching policy dates must cover the experiment's fixed
end horizon. The client rechecks this each minute and revokes a positive status
if verification is lost. Restore occurs on explicit stop/close or completion.
See Microsoft's [pause policy documentation](https://learn.microsoft.com/en-us/windows/deployment/update/waas-configure-wufb).

There is no auto-login, scheduled task or boot service installation. After a
Windows reboot, someone must log in and open the application; it then checks
the orphaned run and offers no blocking recovery confirmation. Operating
DropView at the login/lock screen is not guaranteed. Missing Multiscript Editor
with a potentially running measurement still blocks recovery as described above.

### Before unattended operation

See the [Turkish field test checklist](watchdog_field_test_tr.md) for the
fault-injection order and the evidence to collect.

The automated tests use fake process operations, fake registry, generated files
and offscreen Qt. They do not operate DropView, serial hardware or real update
settings. On a short disposable PAD experiment, verify: successful valve readback
and Flexicon stop / resume at the intended speed; interrupting a measurement wait versus a control step;
new-package exports; missing Bluetooth COM to automatic fallback; DropView hang
and absent-editor refusal; explicit Stop during pending recovery; actual pause
verification/restoration with the intended Windows account. Boot/login startup
requires a separate deployment decision. Do not use a live two-week experiment
as the first fault-injection test.

### Device USB cable disconnected

The shared connection-error handler recognizes the exact DropView messages
`The potentiostat is null` and `Device USB cable disconnected` through native
window text or OCR. It acknowledges only verified DropView dialogs. Native OK
buttons use BM_CLICK without requiring foreground focus; visual OK clicks still
require a verified point in the dialog. A cable-loss observation is retained for
the matching process ID and creation time even after the dialog disappears.

During Discrete PAD, confirmed cable loss interrupts recipe progression and
holds/verifies the real pump using the existing recovery rules. Green connection
and yellow Run indicators cannot override the recorded fault. Reconnection
first sends Ctrl+D and verifies disconnection in the original DropView window,
then uses the normal sequence: the selected Bluetooth address's current COM
port (when available), saved successful/present supported COM ports, and finally
Ctrl+C. Bluetooth radio state does not block USB or automatic fallback.
After reconnecting, the previous editor must reach verified Idle before a fresh
measurement can start. If reconnection after a confirmed link loss fails,
recovery sends a normal WM_CLOSE to the original DropView session even when its
main window responds. It services the verified unsaved-curves warning while
waiting up to eight seconds for process exit. The recorded PID and creation time
are checked throughout; warning or window closure alone is insufficient. Verified
process exit is saved before launching DropView again and reconnecting through
the normal sequence. If normal closure times out, recovery revalidates the
original process ID, creation time and DropView executable, checks cancellation,
and forcibly terminates only that process. Process exit must still be verified
before a new DropView session starts. Failed termination blocks continuation.
This escalation applies after confirmed link loss and failed reconnection; the
separate confirmed-hang policy remains unchanged.

Recovery waits for a valid, fresh CSV matching the new package's export name
before advancing the recipe. It scans every three seconds, tolerating the script's
expected first-export duration plus the greater of 60 seconds or 25 percent.
Old files, different export names and invalid CSV content do not satisfy this
check. Missing data interrupts recovery and holds the pump. Scripts whose export
timing cannot be determined require operator verification. Stop remains active
while waiting. Simulation uses the same DropView/data checks and skips physical
pump/valve commands.

The known `Some curves have not been saved` warning is checked before recovery
Ctrl+D and throughout the disconnection wait. Acknowledgement is scoped to the
original DropView process, verifies foreground focus before Enter, and succeeds
only after that exact dialog has closed. One retry is allowed. An open warning
blocks continuation even if the status indicator says disconnected. After the
warning closes, the connection state is read again before COM attempts; warning
closure alone proves neither disconnection nor process exit. A late successful
acknowledgement allows a bounded extra observation window. A responsive DropView
is not terminated or reported as exited merely because its warning was dismissed.
