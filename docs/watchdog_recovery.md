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

**No automatic restart, Windows Update setting change, COM fallback change or
firmware change is enabled yet.** Phase 2 adds an explicit recovery entry below. Normal recipe
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
also callable by a future supervisor without confirmation dialogs.

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
archive or result dialogs. Normal sessions retain their existing dialogs.

Tests include the generated application script format, fake device readback and
failure paths, lock exclusion, and an offscreen Qt recovery-entry integration.
No test opens a serial port or operates DropView. Real-device validation is still
required before unattended use.

## Phase 3 — independent supervision and Windows policy (pending)

Add separate UI/worker health reporting, stage-specific DropView checks, bounded
retries, existing-port-only COM selection and USB fallback. Plan Windows Update
pause for the experiment duration after checking pending reboot and policy state.
Keep planned recipe shutdowns distinct from failures. Do not assume a Windows
service can operate DropView at the login/lock screen.
