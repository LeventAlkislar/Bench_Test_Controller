# Bench Test Controller Standard Operating Procedure

Version: Draft 1  
Audience: Operators running standard bench tests  
Related document: `docs/user_manual.md`

## 1. Purpose

This SOP describes the standard operator workflow for preparing, running, monitoring, and closing a bench test using Bench Test Controller.

Use the User Manual when you need an explanation of each screen, button, field, or recipe concept. Use this SOP when you are ready to run a test and need the execution sequence.

## 2. Scope

This procedure covers:

1. Pre-run physical and software checks.
2. Device connection checks.
3. Measurement setup.
4. Recipe loading and review.
5. Recipe execution.
6. Live monitoring.
7. Completion, stop, and shutdown.
8. Data verification after the run.

This SOP does not define the validated scientific acceptance criteria for each test family. Acceptance criteria, reference measurements, sensor preparation, and sample handling must come from the approved test protocol.

## 3. Safety and Stop Conditions

Stop the test immediately if any of the following occur:

1. Unexpected leakage, blockage, air bubbles, or fluid path failure.
2. Wrong solution connected to a Valve A port.
3. Pump running in the wrong direction or at an unsafe rate.
4. Valve movement does not match the expected fluidic state.
5. DropView fails to start, connect, measure, stop, or save data.
6. Measurement timing drifts beyond the acceptable interval for the test.
7. Any hardware behaves unexpectedly.

Use `Stop Recipe` for a controlled software stop. Use `Emergency Stop ALL` if immediate hardware stop is required.

## 4. Required Inputs

Before starting, prepare the following:

```text
+--------------------------+-------------------------------------------------------------+
| Input                    | Requirement                                                 |
+--------------------------+-------------------------------------------------------------+
| Part Number              | Unique sensor/sample/part identifier                        |
| Package Root             | Approved folder for storing measurement sessions            |
| Method File              | Correct `.tp` for PAD or `.tc` for CV                       |
| Recipe File              | Approved `.json` recipe for the selected test               |
| Measurement Mode         | Discrete PAD, Continuous PAD, or CV                         |
| Valve A tubing map       | Confirmed physical mapping of ports to solutions            |
| Port - Glucose Mapping   | Must match the physical Valve A tubing                      |
| Experiment Conditions    | Temperature, flow rate, response delay, and related notes   |
| Reference measurement plan | Manual YSI or other reference plan, if required           |
+--------------------------+-------------------------------------------------------------+
```

## 5. Standard Valve A Mapping

Confirm the physical setup before using this mapping.

```text
+--------+-----------------+---------------------------------------+
| Port   | Typical Fluid   | Typical Use                           |
+--------+-----------------+---------------------------------------+
| Port 1 | PBS             | Rinse, flush, stabilization, baseline |
| Port 2 | 40 mg/dL        | Low glucose exposure                  |
| Port 3 | 65 mg/dL        | Low-mid glucose exposure              |
| Port 4 | 125 mg/dL       | Main baseline / reference glucose     |
| Port 5 | 215 mg/dL       | High glucose exposure                 |
| Port 6 | 300 mg/dL       | Higher glucose exposure               |
| Port 7 | 400 mg/dL       | Highest glucose exposure              |
| Port 8 | Not assigned    | Reserved or unused                    |
+--------+-----------------+---------------------------------------+
```

If the physical tubing differs from this table, update `Port - Glucose Mapping` and verify that the selected recipe still matches the intended test.

## 6. Pre-Run Physical Checklist

Complete these checks before opening or starting the recipe:

```text
+------------------------------------------------------------+------+
| Check                                                      | Done |
+------------------------------------------------------------+------+
| Sensor/sample is prepared according to the test protocol.  |      |
| Correct solutions are prepared and labeled.                |      |
| Valve A ports are connected to the intended solutions.     |      |
| Valve B tubing and loop/injection path are ready.          |      |
| Pump tubing is installed and primed.                       |      |
| Waste container has enough capacity.                       |      |
| No visible leaks, trapped air, or blockages are present.   |      |
| DropSens device and electrodes are connected correctly.    |      |
| Manual reference measurement plan is available if needed.  |      |
+------------------------------------------------------------+------+
```

## 7. Start the Application

1. Open Bench Test Controller.
2. Confirm that the main window opens without startup errors.
3. If `Simulation Mode` is shown, confirm whether this is intentional.
4. Do not run a formal hardware test in Simulation Mode unless the protocol explicitly allows it.

## 8. Connect and Check Devices

Open the `Manual Control` tab.

### 8.1 Refresh COM Ports

1. Click `Refresh COM Ports`.
2. Confirm that COM ports appear for Valve A, Valve B, pump, and DropSens as applicable.

### 8.2 Connect Valve A

1. Select the correct `COM Port`.
2. Confirm `Baud` and `Addr (hex)`.
3. Click `Connect A`.
4. Click `Test A`.
5. Confirm the status shows connected and the test succeeds.
6. If needed, click `Query A` and confirm `Current Port`.

### 8.3 Connect Valve B

1. Select the correct `COM Port`.
2. Confirm `Baud` and `Addr (hex)`.
3. Click `Connect B`.
4. Click `Test B`.
5. Confirm the status shows connected and the test succeeds.
6. If needed, click `Query B` and confirm `Current State`.

### 8.4 Connect Pump

1. Select the correct pump `COM Port`.
2. Click `Connect Pump`.
3. Click `Status`.
4. Confirm the pump state is valid.
5. Confirm `RPM`, `Max RPM`, and any physical flow-rate requirement from the test protocol.

### 8.5 Check DropView / DropSens

1. Select the correct DropSens `COM Port`.
2. Click `Start DropView`.
3. Click `Connect`.
4. Confirm the DropView / DropSens status indicator shows connected.

If DropView cannot be controlled automatically, stop and resolve the DropView state before continuing.

## 9. Configure Measurement Setup

Open the `Measurement Setup` tab.

### 9.1 Session Identity

1. Enter the correct `Part Number`.
2. Select the correct `Package Root` using `Browse`.
3. Confirm the `CSV File` path is automatically populated when applicable.
4. Confirm the `Session ID` will be assigned when the session starts.

### 9.2 Measurement Mode

Select the required mode:

```text
+----------------+--------------------------------------------------------------+
| Mode           | Use When                                                     |
+----------------+--------------------------------------------------------------+
| Discrete PAD   | Repeated scripted PAD measurements and CSV aggregation       |
| Continuous PAD | Continuous PAD response tracking with DropView AutoSave data |
| CV             | Cyclic voltammetry using a `.tc` method                      |
+----------------+--------------------------------------------------------------+
```

Select the mode before loading the method file.

### 9.3 Load Method

1. Click `Load Method`.
2. Select the approved `.tp` or `.tc` method file.
3. Confirm the file loads without error.
4. Review visible method parameters.
5. Do not modify method values unless the test protocol allows it.

### 9.4 Script Parameters

For `Discrete PAD`:

1. Confirm `Repeat Count`.
2. Confirm `Wait Duration`.
3. Confirm `Method File`.
4. Confirm `CSV File`.

For `Continuous PAD` and `CV`, script repeat/wait behavior may be disabled because the method is loaded directly.

### 9.5 Experiment Conditions

1. Enter or confirm `Temperature`.
2. Enter or confirm `Flow Rate`.
3. Confirm `Response Delay` if known.
4. Confirm `Port - Glucose Mapping` matches the physical Valve A setup.

## 10. Load and Review the Recipe

Open the `Recipe Control` tab.

1. Click `Load Recipe`.
2. Select the approved recipe `.json` file.
3. Confirm `Recipe Name`.
4. Confirm `Recipe Loops`.
5. Review every row in `Recipe Steps`.
6. Confirm `Recipe Duration` and total expected test time.
7. Confirm step loop information if the recipe uses loops.

During review, verify:

```text
+----------------------+--------------------------------------------------------------+
| Item                 | What to Check                                                |
+----------------------+--------------------------------------------------------------+
| Valve A              | Ports match intended PBS/glucose sequence                    |
| Valve B              | Load/Inject state is correct for the fluidic setup           |
| Pump RPM             | `-`, `0`, or positive RPM matches intended flow behavior     |
| Duration             | Each wait period is appropriate for exposure/stabilization   |
| DropView Action      | Measurement starts, stops, restarts, and exits as intended   |
| Description          | Step intent is clear and matches the hardware commands       |
| Step Loops           | Repeated ranges match the intended test schedule             |
+----------------------+--------------------------------------------------------------+
```

Do not start the recipe if the recipe description and hardware commands do not agree.

## 11. Start the Recipe

1. Confirm the physical setup is ready.
2. Confirm all required devices are connected.
3. Confirm Measurement Setup is complete.
4. Confirm the correct recipe is loaded.
5. Click `Start Recipe`.
6. Watch the `Recipe Execution` status.
7. Confirm the package is created successfully.
8. Confirm the progress bar begins updating during timed steps.

If the application reports a package, method, script, DropView, valve, or pump error, stop and resolve the issue before continuing.

## 12. Monitor the Run

During execution:

1. Keep `Recipe Control` visible when monitoring step progress.
2. Check `Status` and `Progress`.
3. Watch the `Package Log` for packaging, aggregation, or runtime messages.
4. Use `Graphics Viewer` and `Load Active Session` to inspect live data when needed.
5. Use `Refresh` in Graphics Viewer to update the displayed data.
6. Confirm graph markers match expected valve changes and measurement events.
7. Apply or adjust `Response Delay` if marker timing and sensor response are offset.

For long or repeated DropView runs:

1. Expect planned DropView stop/exit/start blocks if the recipe uses them.
2. Treat these blocks as a timing reset workaround.
3. Confirm the measurement interval returns close to the intended repeat/wait interval after restart.
4. Stop the test if timing drift makes the data unsuitable for the protocol.

## 13. Pause, Stop, or Emergency Stop

### 13.1 Pause

Use `Pause` only when a temporary hold is acceptable.

1. Click `Pause`.
2. Confirm the status indicates the recipe is paused.
3. Resolve the reason for pausing.
4. Resume only when the setup is safe and the protocol allows continuation.

### 13.2 Controlled Stop

Use `Stop Recipe` when the test should end in a controlled way.

1. Click `Stop Recipe`.
2. Confirm the recipe stops.
3. Confirm pump and measurement activity are stopped.
4. Wait for final aggregation to complete if it starts.
5. Review logs for stop or abort messages.

### 13.3 Emergency Stop

Use `Emergency Stop ALL` if immediate hardware stop is required.

1. Open `Manual Control` if possible.
2. Click `Emergency Stop ALL`.
3. Verify the pump and valves have stopped or reached a safe state.
4. Manually intervene at the hardware level if software control is not sufficient.
5. Document the event in the test record.

## 14. Complete the Test

When the recipe finishes normally:

1. Confirm `Recipe Execution` reports completion.
2. Confirm `Package Status` reports completion or aggregation status.
3. Confirm final aggregation has run.
4. Confirm DropView has stopped measurement and exited if the recipe includes that action.
5. Confirm the pump is stopped or in the expected final state.
6. Confirm valves are in the expected final state.

## 15. Verify Data Outputs

Open the `Graphics Viewer` tab.

1. Click `Load Active Session` or `Load Session`.
2. Confirm the correct part number/session is loaded.
3. Review `Session Info`.
4. Review `System Events`.
5. Review measurement graphs.
6. Confirm data exists for the expected time range.
7. Confirm event markers match expected recipe steps.
8. Confirm any required xlsx aggregate output exists.

Session output should be located under:

```text
{package_root}/
  {part_number}/
    {YYYYMMDD_HHMMSS}/
      session.json
      measurement_parameters/
      measurements/
      logs/
```

## 16. Save or Record Logs

Open the `Full Log` tab if a separate log copy is required.

1. Review log messages.
2. Click `Time Stamp` if adding a manual timestamp is useful.
3. Click `Save Log` if the operator record requires a saved log file.

Do not clear logs until required records have been reviewed or saved.

## 17. Post-Run Physical Shutdown

After software completion:

1. Confirm measurement has stopped in DropView.
2. Confirm pump is stopped.
3. Confirm valves are in the desired final/safe state.
4. Disconnect or close DropView if needed.
5. Secure solutions, waste, tubing, and sensor according to the lab procedure.
6. Record manual reference values if required.

## 18. Run Record Checklist

Complete this checklist before accepting the run:

```text
+---------------------------------------------------------------+------+
| Check                                                         | Done |
+---------------------------------------------------------------+------+
| Correct Part Number was used.                                |      |
| Correct recipe was loaded.                                   |      |
| Correct measurement mode was selected.                       |      |
| Correct method file was loaded.                              |      |
| Valve A mapping matched physical tubing.                     |      |
| Pump/flow condition matched the test protocol.               |      |
| DropView measurement started successfully.                   |      |
| Planned DropView restarts occurred if required.              |      |
| Recipe completed or stop/abort reason was documented.        |      |
| Measurement files were created.                              |      |
| Aggregated xlsx output was created when required.            |      |
| Logs were reviewed and saved if required.                    |      |
| Manual reference values were recorded if required.           |      |
+---------------------------------------------------------------+------+
```

## 19. Common Abort Reasons to Document

If the run is stopped or invalidated, document the reason clearly:

1. Wrong port/solution connection.
2. Hardware connection failure.
3. Pump or flow-rate mismatch.
4. DropView automation failure.
5. DropView measurement timing drift.
6. Missing or incomplete output files.
7. Incorrect method or recipe selected.
8. Leak, blockage, air bubble, or sensor issue.
9. Manual reference measurement missed.
10. Operator-initiated stop.

## 20. Notes for SOP Finalization

This draft should be reviewed against the approved laboratory protocol. Before release, fill in:

1. Approved default COM port or device identification rules.
2. Approved method file names.
3. Approved recipe file names for each test family.
4. Required temperature and flow-rate values.
5. Required manual reference measurement timing.
6. Acceptance criteria for valid timing, data completeness, and sensor response.
7. Required operator sign-off fields.

