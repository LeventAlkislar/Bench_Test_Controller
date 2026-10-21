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

**No automatic restart, new recovery package, Windows Update setting change,
COM fallback change or firmware change is enabled by phase 1.** Normal recipe
startup still has its original pump stop; it must not be reused as a recovery
startup path. If a recording warning occurs, an old snapshot must not be treated
as proof of current device state.

Validation (no hardware/UI operation):

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tools -p test_*.py
```

## Phase 2 — recovery executor (pending)

Create a new linked session/package from saved method files and the live recipe,
repoint script output paths, reconnect without homing/resetting devices, read
actual valve positions, reconcile the old measurement, and continue from the
planned cursor without invoking normal startup preparation or zeroing the pump.
An inherited running measurement must be explicitly reconciled even when the
next step has `dropview_action=none`.

Also retain stop intent when an operation is blocked or finalization fails.
Prevent a second recipe/executor from controlling the same devices concurrently.

## Phase 3 — independent supervision and Windows policy (pending)

Add separate UI/worker health reporting, stage-specific DropView checks, bounded
retries, existing-port-only COM selection and USB fallback. Plan Windows Update
pause for the experiment duration after checking pending reboot and policy state.
Keep planned recipe shutdowns distinct from failures. Do not assume a Windows
service can operate DropView at the login/lock screen.
