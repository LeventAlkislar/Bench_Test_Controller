# Discrete PAD watchdog: implementation phases

## Phase 1 — durable execution records (implemented)

Normal Discrete PAD starts create `logs/recipe_recovery.json` in the session.
The session file registry contains its path under `recipe_recovery`.
Simulation, Continuous PAD, CV and recipes containing those actions do not
create this record through the normal UI startup path.

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

- A running Discrete PAD measurement in the wait phase advances to the next
  sequence item, ignoring its remaining time.
- Start DropView, Exit DropView and measurement stop actions are never skipped
  merely because their step contains a wait.
- Unfinished device operations remain at the same step. Replaying their device
  commands is **not** authorized by a cursor alone; phase 2 must reconcile them.
- A completed or disabled step advances exactly once.
- Outer-loop boundaries and expanded inner loops retain their position.
- The last step of the final loop produces `finished`, not a fresh recipe run.
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
Failures during setup leave that source eligible for a new explicit attempt;
the record retains whether the old measurement stop was already verified.

Valve reconnection uses saved serial settings and position queries, never reset
or home. A different position blocks recovery unless that exact movement was
interrupted and still needs its target applied. A target already reached is not
commanded again. Settings applied before an interrupted operation are not
replayed. A control step's wait retains its recorded remaining duration; a
running measurement wait advances according to the phase 1 rule.

An inherited measurement is stopped and verified before a fresh measurement is
started in the new package, including when the next step has action `none`.
**If a potentially active measurement has no accessible Multiscript Editor,
recovery blocks.** The existing stop function returning because there is no
editor is not proof of a stopped device. A further confirmed device feedback
path is needed to cover all DropView crashes automatically.

Recovery does not invoke the normal recipe-start pump STOP/SPEED sequence.
Flexicon reconnect uses its existing status-only connection path; Arduino
serial reopen is blocked because it resets the Nano. No firmware changes are
made. Normal completion and an explicit user stop retain their existing pump
stop behavior. A Discrete PAD operation failure with a recovery journal now
preserves pump output, rather than stopping it before recovery can run.

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

## Phase 3 — independent supervision (implemented; hardware validation pending)

Real, journal-backed Discrete PAD recipes automatically arm `WatchdogClient`.
Continuous PAD, CV and simulation do not arm automatic recovery. The client
starts the same interpreter/executable with `--watchdog`; this separate process
observes JSON state and requests cooperative interruption. It never sends device
commands. If the owner remains hung, it checks the latest recoverable record and
PID creation time before terminating that application, then starts
`--watchdog-recover TOKEN`. Other applications and DropView are not terminated by
the supervisor. Device reconciliation belongs to the recovery executor.

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
| Fresh missing, unresponsive or disconnected DropView | 30-second confirmation |
| Cooperative interruption | 60 seconds; recovery startup 180 seconds |
| Consecutive recovery attempts | At most 3; 30/60-second retry backoff |
| Retry budget reset | 5 minutes of healthy connected measurement wait |

Planned DropView closures are not connection faults. Covered/minimized windows,
unknown image matches and stale health samples are reported as unverified rather
than treated as disconnected. A single bounded background CSV probe observes file
metadata each minute. After learning at least two write intervals, a gap exceeding
both 20 minutes and five times the longest learned interval requests recovery.
Without a learned cadence, 30 minutes without sufficient data produces a warning.
Storage errors and a stuck probe also produce warnings, not device commands.

User Stop/close first signals a named Windows event, fencing pending launches
even if writing stop intent to disk blocks. Pause, terminal journals and changed
process owners forbid automatic recovery. A journal failure disables recovery
and reports it. The update pause is retained while the physical experiment may
still be running. Recovery waits for the old worker and package aggregation to
finish before adopting a new package.

Manual DropSens selection now uses only currently present ports supported by
the existing image templates (COM3/COM4/COM10). A port disappearing between scan
and connection is skipped before opening the dialog. No available supported
port goes directly to Ctrl+C automatic fallback. The last verified manual or
automatic route is remembered; automatic is tried first next time. Ctrl+C's
actual USB/Bluetooth transport is not independently queried.

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

The automated tests use fake process operations, fake registry, generated files
and offscreen Qt. They do not operate DropView, serial hardware or real update
settings. On a short disposable PAD experiment, verify: successful valve readback
and unchanged pump output; interrupting a measurement wait versus a control step;
new-package exports; missing Bluetooth COM to automatic fallback; DropView hang
and absent-editor refusal; explicit Stop during pending recovery; actual pause
verification/restoration with the intended Windows account. Boot/login startup
requires a separate deployment decision. Do not use a live two-week experiment
as the first fault-injection test.
