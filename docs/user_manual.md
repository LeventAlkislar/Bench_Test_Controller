# Bench Test Controller User Manual

Version: Draft 1  
Audience: Operators, test engineers, and technical users  
Scope: Application navigation, screen functions, measurement setup, recipe control, data viewing, and logs

> Screenshot placeholder: Main application window

## 1. Introduction

Bench Test Controller is a desktop application used to control and monitor bench tests involving valves, a peristaltic pump, and DropSens / DropView measurement software. The application brings manual hardware control, measurement package preparation, recipe execution, data visualization, and logging into one operator interface.

This manual explains what each tab, menu area, button, and input field is used for. It is intended to help a new operator understand the application before running a formal test procedure.

A separate Standard Operating Procedure should be used for the exact step-by-step execution of a validated test.

## 2. Application Overview

The main window contains five tabs:

```text
+-------------------+-------------------------------------------------------------------------------------------------------------+
| Tab               | Purpose                                                                                                     |
+-------------------+-------------------------------------------------------------------------------------------------------------+
| Manual Control    | Connect, test, and manually control valves, pump, and DropView.                                             |
| Measurement Setup | Define the part number, package location, measurement method, script parameters, and experiment conditions. |
| Recipe Control    | Create, load, save, and execute automated recipe sequences.                                                 |
| Graphics Viewer   | Load active or archived measurement sessions and inspect measurement graphs, notes, and system events.      |
| Full Log          | View, clear, save, and timestamp the application log.                                                       |
+-------------------+-------------------------------------------------------------------------------------------------------------+
```

Two banners may appear above the tabs:

```text
+-----------------+---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| Banner          | Meaning                                                                                                                                                                         |
+-----------------+---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| Simulation Mode | Valve connection is missing and valve steps will be skipped. This is useful for dry-run behavior but should not be treated as a normal hardware test unless intentionally used. |
| Part banner     | Shows the currently active or displayed part number when a session is active, archived, or loaded.                                                                              |
+-----------------+---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
```

## 3. Main Window and Navigation

Use the tab bar at the top of the application to switch between major work areas. The usual workflow is:

1. Use Manual Control to confirm that hardware and DropView can be reached.
2. Use Measurement Setup to define the session and measurement configuration.
3. Use Recipe Control to load or build a recipe and start execution.
4. Use Graphics Viewer to monitor or review measurement data.
5. Use Full Log when detailed troubleshooting or record review is needed.

Changing tabs does not automatically stop a running recipe. Always use the controls in Recipe Control or Manual Control when you need to pause, stop, or emergency-stop hardware activity.

## 4. Manual Control Tab

> Screenshot placeholder: Manual Control tab

The Manual Control tab is used to connect to the hardware and manually operate each device. It is also the primary place to confirm COM port settings before automated recipe execution.

### 4.1 Valve A: SV-01 Multiport Valve

Valve A is an 8-port selector valve.

```text
+-------------------+--------------------------------------------------------------------------------------------+
| Control           | Description                                                                                |
+-------------------+--------------------------------------------------------------------------------------------+
| COM Port          | Selects the serial COM port assigned to Valve A.                                           |
| Baud              | Selects the serial baud rate. Common values include 9600, 19200, 38400, 57600, and 115200. |
| Addr (hex)        | Sets the valve address in hexadecimal format.                                              |
| Connect A         | Opens the serial connection to Valve A.                                                    |
| Disconnect A      | Closes the Valve A connection.                                                             |
| Test A            | Sends a connection test command and reports whether the valve responds.                    |
| Speed (5-350 rpm) | Sets the target valve rotation speed.                                                      |
| Presets           | Quickly selects Slow, Medium, Fast, or Max speed values.                                   |
| Set Temporary     | Applies the selected speed until the device is powered off.                                |
| Set Permanent     | Stores the selected speed permanently on the device. A restart may be required.            |
| Query Speed       | Reads the current speed setting from the valve.                                            |
| Port 1 - Port 8   | Moves Valve A to the selected port.                                                        |
| Current Port      | Displays the last known or queried port position.                                          |
| Reset A           | Sends a reset command to Valve A.                                                          |
| Stop A            | Sends a stop command to Valve A.                                                           |
| Query A           | Queries the current Valve A status.                                                        |
+-------------------+--------------------------------------------------------------------------------------------+
```

Use Valve A manual controls carefully. Manual port switching may affect fluid paths and should be performed only when the physical setup is safe for the requested port change.

Operational meaning:

Valve A selects which inlet line is connected to the measurement flow path. In the standard glucose test setup, these ports are typically connected to different glucose solutions, such as baseline solution and increasing glucose concentrations. Selecting Port 1 through Port 8 therefore changes which solution is introduced into the system.

In recipes, a Valve A value of `0` means no change. In operator language this is equivalent to keeping the previous fluid path active. Use Valve A port changes when the test needs to expose the sensor to a new solution or concentration. The physical tubing connected to each port should match the `Port - Glucose Mapping` values in Measurement Setup.

### 4.2 Valve B: SY-07B Injector Valve

Valve B is a 6-port, 2-state injector valve.

```text
+---------------+-------------------------------------------------------------------------+
| Control       | Description                                                             |
+---------------+-------------------------------------------------------------------------+
| COM Port      | Selects the serial COM port assigned to Valve B.                        |
| Baud          | Selects the serial baud rate.                                           |
| Addr (hex)    | Sets the valve address in hexadecimal format.                           |
| Connect B     | Opens the serial connection to Valve B.                                 |
| Disconnect B  | Closes the Valve B connection.                                          |
| Test B        | Sends a connection test command and reports whether the valve responds. |
| Load          | Moves the injector valve to Load state: 1-6, 2-3, 4-5.                  |
| Inject        | Moves the injector valve to Inject state: 1-2, 3-4, 5-6.                |
| Current State | Displays the last known valve state.                                    |
| Reset B       | Sends a reset command to Valve B.                                       |
| Stop B        | Sends a stop command to Valve B.                                        |
| Query B       | Queries the current Valve B status.                                     |
+---------------+-------------------------------------------------------------------------+
```

Operational meaning:

Valve B controls whether the injector valve is in the `Load` or `Inject` fluidic state. `Load` is typically used to prepare or fill the injection loop without sending the prepared volume into the measurement path. `Inject` is used when the prepared volume should be introduced into the measurement path.

Use Valve B steps when the test protocol requires a defined injection event. If the recipe should only keep the existing injector state, select no change.

### 4.3 Peristaltic Pump

The pump is controlled through an Arduino Nano and MCP4725 output.

```text
+-------------------+---------------------------------------------------------------------+
| Control           | Description                                                         |
+-------------------+---------------------------------------------------------------------+
| COM Port          | Selects the serial COM port assigned to the pump controller.        |
| Baud: 115200      | Shows the fixed pump baud rate.                                     |
| Connect Pump      | Opens the pump controller connection.                               |
| Disconnect Pump   | Closes the pump controller connection.                              |
| Status            | Queries the pump controller status.                                 |
| Speed mV (0-5000) | Sets the analog output voltage in millivolts.                       |
| Set mV            | Applies the selected millivolt output.                              |
| Max RPM           | Defines the pump maximum RPM calibration value used by RPM control. |
| Set Max           | Applies the maximum RPM setting.                                    |
| RPM               | Selects the target pump speed in RPM.                               |
| Set RPM           | Applies the selected RPM speed.                                     |
| Pulse (ms)        | Sets pulse duration in milliseconds.                                |
| Pulse FWD         | Runs the pump forward for the selected pulse duration.              |
| Pulse REV         | Runs the pump in reverse for the selected pulse duration.           |
| Run FWD           | Starts continuous forward pump motion.                              |
| Run REV           | Starts continuous reverse pump motion.                              |
| Stop Pump         | Stops the pump.                                                     |
| Pump state        | Displays whether the pump is connected and its current activity.    |
+-------------------+---------------------------------------------------------------------+
```

The pump note in the interface indicates that pump speed is controlled by MCP4725 VOUT to Pump Pin 9, while stop and direction are controlled through Arduino Nano pins D8 and D7.

Operational meaning:

The pump controls movement of fluid through the system. Pump speed affects how quickly the selected solution reaches the sensor and how strongly the system is flushed between solution changes. A higher RPM usually moves liquid faster, while a lower RPM gives slower flow and longer residence time.

In recipes, Pump RPM has three practical meanings: `-` keeps the previous pump state, `0` stops the pump, and a positive value runs the pump forward at that RPM. Use pump changes when a step needs to start flow, stop flow, flush a line, or maintain a stable flow during measurement.

### 4.4 DropSens - DropView 8400M

This section controls the external DropView application and the DropSens connection.

```text
+------------------+--------------------------------------------------------+
| Control          | Description                                            |
+------------------+--------------------------------------------------------+
| COM Port         | Selects the COM port used for the DropSens connection. |
| Start DropView   | Launches the DropView software.                        |
| Connect          | Establishes the DropSens connection in DropView.       |
| Disconnect       | Disconnects DropSens from DropView.                    |
| Exit DropView    | Closes DropView.                                       |
| Status indicator | Shows whether DropView / DropSens appears connected.   |
+------------------+--------------------------------------------------------+
```

DropView operations depend on the external DropView application being installed and visible to the automation layer.

### 4.5 General

```text
+--------------------+-----------------------------------------------------------------------------------------------------+
| Control            | Description                                                                                         |
+--------------------+-----------------------------------------------------------------------------------------------------+
| Refresh COM Ports  | Refreshes the available serial COM port list for connected devices.                                 |
| Emergency Stop ALL | Attempts to stop all connected controllable hardware. Use this when immediate shutdown is required. |
| Query All Status   | Queries available status information from all connected devices.                                    |
| Connection Summary | Displays a compact status summary for the configured devices.                                       |
+--------------------+-----------------------------------------------------------------------------------------------------+
```

## 5. Measurement Setup Tab

> Screenshot placeholder: Measurement Setup tab

The Measurement Setup tab prepares the measurement session. It is divided into three main columns:

```text
+--------+-------------------+-----------------------------------------------------------------------------------------------+
| Column | Panel             | Purpose                                                                                       |
+--------+-------------------+-----------------------------------------------------------------------------------------------+
| Left   | PackagePanel      | Defines session identity, package root, file references, package status, and package log.     |
| Center | MethodEditorPanel | Loads and displays DropView method parameters.                                                |
| Right  | ScriptParamsPanel | Defines measurement mode, script parameters, experiment conditions, and port-glucose mapping. |
+--------+-------------------+-----------------------------------------------------------------------------------------------+
```

### 5.1 Session Identity

```text
+--------------+-----------------------------------------------------------------------------------------------------------------------------------+
| Field        | Description                                                                                                                       |
+--------------+-----------------------------------------------------------------------------------------------------------------------------------+
| Part Number  | Operator-entered identifier for the sensor, sample, or part under test. This value is used in package paths and output filenames. |
| Session ID   | Automatically assigned when a measurement session starts.                                                                         |
| Package Root | Root folder where measurement packages are created.                                                                               |
| Browse       | Selects the package root folder.                                                                                                  |
+--------------+-----------------------------------------------------------------------------------------------------------------------------------+
```

The application remembers the most recently used part number and package root.

### 5.2 File References

```text
+---------------------+------------------------------------------------------------------+
| Reference           | Description                                                      |
+---------------------+------------------------------------------------------------------+
| Method File (.tp)   | Shows the loaded DropView PAD method file, or `.tc` for CV mode. |
| Script File (.scr)  | Shows the generated or packaged script file when applicable.     |
| Recipe File (.json) | Shows the currently loaded recipe file when available.           |
+---------------------+------------------------------------------------------------------+
```

File reference indicators turn green when the referenced file exists. Missing referenced files are shown as not found.

### 5.3 Package Status

```text
+------------------------+---------------------------------------------------------------------------------------------------------+
| Control                | Description                                                                                             |
+------------------------+---------------------------------------------------------------------------------------------------------+
| Package status label   | Shows whether the package has been created, is running, completed, or has an error.                     |
| Session directory      | Shows the created measurement session folder.                                                           |
| Aggregate CSVs -> xlsx | Merges measurement CSV files into an xlsx output. This also runs automatically when a recipe completes. |
+------------------------+---------------------------------------------------------------------------------------------------------+
```

### 5.4 Package Log

The Package Log displays setup, packaging, aggregation, and session-related messages. Use it to confirm whether required files were copied, generated, or aggregated successfully.

### 5.5 Clear Session

`Clear Session` resets the currently displayed session data in the interface, including recipe, script, log, and chart views. Use this before preparing a new unrelated session.

### 5.6 Method Editor

> Screenshot placeholder: Method editor panel

Use `Load Method` to select a DropView method file.

```text
+-------------------------+---------------------------------------------------------------------------------------------------------------------------------+
| Area                    | Description                                                                                                                     |
+-------------------------+---------------------------------------------------------------------------------------------------------------------------------+
| File label              | Shows the loaded method file path or indicates that no file is loaded.                                                          |
| Technique label         | Shows the detected method technique, such as Pulsed Amperometric Detection or CV.                                               |
| Node label              | Shows the node information from the method file.                                                                                |
| Options                 | Displays method options such as Leave cell on and Standby potential.                                                            |
| Pretreatment            | Displays pretreatment parameters such as Econd, tcond, Edep, tdep, and tequil.                                                  |
| Measurement             | Displays editable measurement parameters such as E1, t1, E2, t2, E3, t3, E4, t4, E5, t5, ti, t, Estep, Srate, and nscans.       |
| Multichannel Parameters | Displays channel-related information and editable electrical parameters for supported modes.                                    |
| Information             | Allows Sensor and Sample metadata to be edited. The Sample value is automatically synchronized from Part Number when available. |
+-------------------------+---------------------------------------------------------------------------------------------------------------------------------+
```

The expected method type depends on the selected measurement mode. PAD modes expect a PAD method, while CV mode expects a CV method.

Operational meaning:

The method file defines the electrochemical measurement behavior used by DropView. The application displays and edits selected method parameters so the operator can confirm that the electrical settings match the intended test before the recipe starts.

Changing method parameters changes how the sensor is stimulated and how the current response is recorded. Treat method changes as test-method changes, not only software settings. If a validated method is required, load the approved method file and avoid changing values unless the test plan allows it.

### 5.7 Measurement Mode

```text
+----------------+----------------------------------------------------------------------------------------------------------------------------------+
| Mode           | Behavior                                                                                                                         |
+----------------+----------------------------------------------------------------------------------------------------------------------------------+
| Discrete PAD   | Generates a `.scr` script and exports CSV files during measurement. Repeat Count and Wait Duration are available.                |
| Continuous PAD | Loads the `.tp` method directly. DropView AutoSave `.mtp` files are used. Live CSV plotting and script repeat/wait are disabled. |
| CV             | Loads the `.tc` method directly. DropView AutoSave `.mtc` files are used. Run CV waits until the method finishes.                |
+----------------+----------------------------------------------------------------------------------------------------------------------------------+
```

Operational meaning:

The measurement mode determines how the application interacts with DropView and what type of output files are expected. Select `Discrete PAD` when the test should collect repeated scripted PAD measurements and export CSV data. Select `Continuous PAD` when the test should run longer PAD segments and rely on DropView AutoSave files. Select `CV` when the recipe should run cyclic voltammetry using a CV method.

Choose the mode before loading the method file. The selected mode changes which method type is expected and which recipe DropView actions make sense.

### 5.8 Script Parameters

```text
+---------------+-------------------------------------------------------------------------+
| Field         | Description                                                             |
+---------------+-------------------------------------------------------------------------+
| Repeat Count  | Number of repeated script measurements for Discrete PAD mode.           |
| Wait Duration | Waiting time between repeated actions in seconds.                       |
| Method File   | Read-only field filled when a method is loaded.                         |
| CSV File      | Read-only field filled when Part Number and Package Root are available. |
+---------------+-------------------------------------------------------------------------+
```

Operational meaning:

`Repeat Count` and `Wait Duration` are used in Discrete PAD mode to control repeated measurements. Repeat Count determines how many acquisitions are requested. Wait Duration defines the delay between repeated actions. Together, these values affect how many data points are collected and how much time is allowed between acquisitions.

Use larger repeat counts when the test needs more repeated readings for stability or averaging. Use wait duration when the sensor or fluidic system needs time between readings.

### 5.9 Experiment Conditions

```text
+----------------+----------------------------------------------------------------------------------+
| Field          | Description                                                                      |
+----------------+----------------------------------------------------------------------------------+
| Temperature    | Experiment temperature in degrees Celsius.                                       |
| Flow Rate      | Flow rate in mL/min.                                                             |
| Response Delay | Read-only display synchronized from the Graphics Viewer response delay controls. |
+----------------+----------------------------------------------------------------------------------+
```

These values are stored with the session so archived data can be reviewed with its experiment context.

Operational meaning:

Experiment Conditions are metadata for interpreting the result. Temperature and flow rate do not directly control external hardware from this panel, but they record the conditions under which the session was run. This is important when comparing archived sessions, troubleshooting sensor response, or preparing reports.

### 5.10 Port - Glucose Mapping

The Port - Glucose Mapping section assigns a glucose concentration to Valve A ports 1 through 8. A blank value indicates that no glucose concentration is assigned for that port.

The default mapping is:

```text
+--------+---------------+
| Port   | Default Value |
+--------+---------------+
| Port 1 | 0 mg/dL       |
| Port 2 | 40 mg/dL      |
| Port 3 | 65 mg/dL      |
| Port 4 | 125 mg/dL     |
| Port 5 | 215 mg/dL     |
| Port 6 | 300 mg/dL     |
| Port 7 | 400 mg/dL     |
| Port 8 | Not assigned  |
+--------+---------------+
```

Operational meaning:

This mapping tells the application and the operator what solution concentration is expected at each Valve A port. It should match the physical tubing and bottles connected to the valve. For example, if Port 2 is connected to a 40 mg/dL glucose solution, Port 2 should also be set to 40 mg/dL in this table.

This mapping is especially useful when reviewing graphs, because valve port changes can be interpreted as concentration changes. If the physical setup and the mapping do not match, the graph may be labeled or interpreted incorrectly.

## 6. Recipe Control Tab

> Screenshot placeholder: Recipe Control tab

The Recipe Control tab is used to build, edit, save, load, and execute automated test recipes.

### 6.1 Recipe Settings

```text
+--------------+----------------------------------------------------------+
| Field        | Description                                              |
+--------------+----------------------------------------------------------+
| Recipe Name  | Name of the recipe shown in logs and saved recipe files. |
| Recipe Loops | Number of times the full recipe step list will repeat.   |
+--------------+----------------------------------------------------------+
```

### 6.2 Add Recipe Step

Each recipe step may control Valve A, Valve B, the pump, DropView, and the wait duration.

```text
+----------------+----------------------------------------------------------------------------------------------------------------+
| Field          | Description                                                                                                    |
+----------------+----------------------------------------------------------------------------------------------------------------+
| Valve A        | Selects a Valve A port from 1 to 8. A value of 0 means no Valve A change.                                      |
| Valve B        | Selects no change, Load, or Inject.                                                                            |
| Pump RPM       | Selects pump behavior. `-` means no change, `0` stops the pump, and a positive value runs forward at that RPM. |
| Duration (min) | Step duration in minutes. A value of 0 is normally useful only for immediate DropView action steps.            |
| Description    | Free-text description of the step.                                                                             |
| DropView       | Selects a DropView action for the step.                                                                        |
| Add Step       | Adds the configured step to the recipe table.                                                                  |
+----------------+----------------------------------------------------------------------------------------------------------------+
```

Operational meaning:

A recipe step is a timed test state. It defines which solution path should be active, what injector valve state should be used, whether the pump should continue or change speed, whether DropView should start or stop a measurement, and how long the system should remain in that state.

Use `Valve A` to select the inlet solution or glucose concentration. Use `0` when the recipe should keep the previous Valve A position. Use `Valve B` when the step needs a load or injection event. Use `Pump RPM` to keep flow unchanged (`-`), stop the pump (`0`), or run forward at a defined speed. Use `Duration` to give the fluidic system and sensor response enough time at the selected condition.

The `Description` field should explain the intent of the step, such as `Baseline`, `40 mg/dL glucose`, `Flush`, or `Start measurement`. Clear descriptions make logs and graphs easier to interpret later.

Available DropView actions are:

```text
+----------------------+--------------------------------------------------------------+
| Action               | Description                                                  |
+----------------------+--------------------------------------------------------------+
| None                 | No DropView action is performed.                             |
| Start DropView       | Launches DropView and connects DropSens.                     |
| Start Discrete PAD   | Starts a discrete PAD measurement using the packaged script. |
| Stop Discrete PAD    | Stops a discrete PAD measurement.                            |
| Start Continuous PAD | Starts a continuous PAD segment using the packaged method.   |
| Stop Continuous PAD  | Stops the continuous PAD segment.                            |
| Run CV               | Runs a CV measurement and waits for completion.              |
| Exit DropView        | Closes DropView.                                             |
+----------------------+--------------------------------------------------------------+
```

Operational meaning:

DropView actions define when electrochemical data collection starts and stops. A fluidic step can use `None` if it only changes valves or pump state. A measurement step should include the appropriate start action, such as `Start Discrete PAD`, `Start Continuous PAD`, or `Run CV`. Stop actions should be used when the measurement segment is complete.

In many recipes, DropView is started near the beginning of a measurement block, then stopped, closed, and started again at planned points. This is mainly used as a timing reset workaround. During long or repeated DropView runs, the actual interval between measurements may gradually become longer than the configured interval. Restarting DropView periodically starts the acquisition sequence fresh so the measurement interval returns closer to the intended timing.

Recipe step execution order is:

1. DropView start-type actions are handled when needed.
2. Valve A is switched if requested.
3. Valve B is switched if requested.
4. Pump RPM is applied if requested.
5. The step duration wait is performed.
6. DropView stop or exit actions are handled when needed.

### 6.3 Recipe Steps Table

The table shows the complete recipe. Columns include:

```text
+-----------------+--------------------------------------------+
| Column          | Description                                |
+-----------------+--------------------------------------------+
| Step #          | Step order in the recipe.                  |
| Valve A         | Valve A port command.                      |
| Valve B         | Valve B state command.                     |
| Pump RPM        | Pump command for the step.                 |
| Duration (min)  | Step duration.                             |
| Description     | Operator-defined description.              |
| DropView Action | DropView command associated with the step. |
| Script (.scr)   | Script reference when applicable.          |
| Loop Info       | Step loop information.                     |
+-----------------+--------------------------------------------+
```

Double-click supported cells to edit values directly.

### 6.4 Step Editing Buttons

```text
+-----------------+-------------------------------------------------+
| Button          | Description                                     |
+-----------------+-------------------------------------------------+
| Duplicate       | Duplicates the selected step.                   |
| Move Up         | Moves the selected step earlier in the recipe.  |
| Move Down       | Moves the selected step later in the recipe.    |
| Remove Selected | Removes selected steps from the recipe.         |
| Clear All       | Removes all recipe steps.                       |
| Set Step Loop   | Creates a loop range for selected recipe steps. |
| Clear Loops     | Removes defined step loop ranges.               |
+-----------------+-------------------------------------------------+
```

Step loops repeat a selected range of steps. Overlapping step loop ranges are not allowed.

### 6.5 Recipe Duration

The Recipe Duration panel summarizes:

```text
+-------------------+-------------------------------------------------------------------------------+
| Field             | Description                                                                   |
+-------------------+-------------------------------------------------------------------------------+
| Step loop summary | Shows defined step loop ranges.                                               |
| Total Time        | Shows the calculated total recipe time including recipe loops and step loops. |
+-------------------+-------------------------------------------------------------------------------+
```

### 6.6 Recipe Files

```text
+-------------+---------------------------------------------+
| Button      | Description                                 |
+-------------+---------------------------------------------+
| Load Recipe | Loads a recipe `.json` file.                |
| Save Recipe | Saves the current recipe as a `.json` file. |
+-------------+---------------------------------------------+
```

Recipes are stored as JSON files and can be reused for repeated test protocols.

### 6.7 Recipe Execution

```text
+--------------+---------------------------------------------------------------------------------------------------------------+
| Control      | Description                                                                                                   |
+--------------+---------------------------------------------------------------------------------------------------------------+
| Start Recipe | Builds the measurement package, prepares the session, and starts the recipe runner.                           |
| Pause        | Pauses the running recipe. In Continuous PAD mode, the active segment may be stopped and restarted on resume. |
| Stop Recipe  | Stops recipe execution. The application attempts to stop pump and measurement activity safely.                |
| Status       | Shows current execution status.                                                                               |
| Progress     | Shows percent progress during timed steps.                                                                    |
+--------------+---------------------------------------------------------------------------------------------------------------+
```

When a recipe finishes or is stopped, the application runs final aggregation and returns the session state to idle.

## 7. Graphics Viewer Tab

> Screenshot placeholder: Graphics Viewer tab with loaded session

The Graphics Viewer tab displays measurement data and session annotations.

### 7.1 Toolbar

```text
+---------------------+-------------------------------------------------------------------------------------------------------+
| Control             | Description                                                                                           |
+---------------------+-------------------------------------------------------------------------------------------------------+
| Session label       | Shows the currently loaded session or indicates that no session is loaded.                            |
| Load Active Session | Loads the currently active measurement session from Measurement Setup.                                |
| Refresh             | Refreshes the currently loaded data. If no session is loaded, it attempts to load the active session. |
| Load Session        | Opens an existing session folder.                                                                     |
| Load DropSens PAD   | Opens a standalone DropSens PAD `.mtp` file.                                                          |
| Delete Session      | Deletes the loaded session folder after user confirmation.                                            |
| Live indicator      | Shows live polling status for active sessions.                                                        |
| Response Delay      | Applies a time offset between valve/recipe events and measurement response interpretation.            |
+---------------------+-------------------------------------------------------------------------------------------------------+
```

Use `Delete Session` carefully. It permanently removes the selected session directory after confirmation.

Operational meaning:

`Response Delay` helps align recipe events with the measured sensor response. In a fluidic system, a valve change does not always reach the sensor immediately. Liquid needs time to travel from the selected inlet, through the tubing and valve path, to the measurement cell. The response delay setting represents this transport and response lag.

Use Response Delay when graph markers appear earlier than the actual current response. For example, if Valve A switches to a glucose solution and the current response begins 20 seconds later, set a 20 second delay so interpretation of the response better matches the physical system.

### 7.2 Annotation Panel

```text
+---------------+----------------------------------------------------------------------------------------------+
| Area          | Description                                                                                  |
+---------------+----------------------------------------------------------------------------------------------+
| Session Info  | Displays part number, session status, creation time, measurement mode, and related metadata. |
| User Notes    | Displays notes stored with the session.                                                      |
| System Events | Displays major events such as started, completed, stopped, error, paused, and resumed.       |
+---------------+----------------------------------------------------------------------------------------------+
```

### 7.3 Graph Area

```text
+---------------------------+---------------------------------------------------------------------------+
| Graph                     | Description                                                               |
+---------------------------+---------------------------------------------------------------------------+
| Measurement Timeline      | Shows measurement current over time, with event markers and step context. |
| Mean Measurement Timeline | Shows mean measurement timeline data when available.                      |
+---------------------------+---------------------------------------------------------------------------+
```

The graph supports zoom and pan through the pyqtgraph interaction controls. Event markers help relate measurement data to recipe steps, valve changes, reset markers, and system events.

### 7.4 PAD and CV View

When a session contains more than one supported measurement type, the viewer can show PAD or CV mode using the `PAD` and `CV` view buttons.

## 8. Full Log Tab

> Screenshot placeholder: Full Log tab

The Full Log tab displays application-wide log messages.

```text
+------------+----------------------------------------+
| Control    | Description                            |
+------------+----------------------------------------+
| Clear Log  | Clears the current displayed log text. |
| Save Log   | Saves the displayed log to a file.     |
| Time Stamp | Inserts a timestamp into the log.      |
+------------+----------------------------------------+
```

Use this tab when troubleshooting connection, packaging, recipe execution, DropView automation, or aggregation behavior.

## 9. Measurement Modes

### 9.1 Discrete PAD

Discrete PAD mode generates a `.scr` script and exports CSV files during measurement. Use this mode when the measurement should run as repeated scripted acquisitions with Repeat Count and Wait Duration.

Typical use:

Use Discrete PAD when the test protocol needs repeated individual PAD measurements, controlled repeat count, and CSV output for aggregation. This mode is useful when each measurement cycle is treated as a discrete reading and later combined into an xlsx result.

Required setup:

1. Select `Discrete PAD`.
2. Load a compatible PAD `.tp` method.
3. Enter Part Number.
4. Select Package Root.
5. Confirm Repeat Count and Wait Duration.
6. Use a recipe with appropriate DropView start and measurement actions.

### 9.2 Continuous PAD

Continuous PAD mode loads the `.tp` method directly and relies on DropView AutoSave `.mtp` files. Script repeat and wait fields are disabled in this mode.

Typical use:

Use Continuous PAD when the goal is to observe the sensor response continuously while the recipe changes fluids, concentrations, pump state, or valve state. This is useful for watching transitions, stabilization behavior, plateaus, and delayed response after solution changes.

Required setup:

1. Select `Continuous PAD`.
2. Load a compatible PAD `.tp` method.
3. Enter Part Number.
4. Select Package Root.
5. Use recipe steps that start and stop continuous PAD segments.

### 9.3 CV

CV mode loads a `.tc` method directly and relies on DropView AutoSave `.mtc` files. The recipe `Run CV` action waits until the method finishes.

Typical use:

Use CV when the test requires cyclic voltammetry rather than PAD current-time acquisition. In this mode, recipe timing should account for the fact that `Run CV` waits for the CV method to complete before continuing to the next step.

Required setup:

1. Select `CV`.
2. Load a compatible CV `.tc` method.
3. Enter Part Number.
4. Select Package Root.
5. Use a recipe step with `Run CV`.

## 10. Standard Test Scenario Concepts

Recipe files are more than software command lists. They describe the test intent as a sequence of fluidic states, measurement states, and waiting periods. The application executes these steps, while the operator uses the descriptions, logs, and graphs to understand what happened during the test.

### 10.1 Typical Valve A Port Meaning

In the available scenario files, Valve A ports are commonly used as solution selectors. The typical standard glucose setup is:

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

This mapping should be confirmed against the physical tubing before each test. If the tubing is changed, update `Port - Glucose Mapping` in Measurement Setup and make sure the recipe still represents the intended sequence.

### 10.2 Typical Recipe Flow

Most standard recipe files follow this pattern:

1. Start DropView.
2. Start the measurement using the selected mode.
3. Select a solution with Valve A.
4. Keep Valve B in the required state, often `Load` for short exposure tests.
5. Wait for the planned exposure, rinse, stabilization, plateau, response, or long-term phase duration.
6. Stop the measurement.
7. Exit DropView.

Some recipes start and stop DropView multiple times. This is intentional. It is mainly used to reduce DropView timing drift during long or repeated measurements. If too many acquisitions are allowed to accumulate in one DropView run, the real interval between measurements may exceed the configured interval and the measurement quality can degrade. Restarting DropView resets the measurement sequence and helps keep the acquisition interval closer to the defined value.

### 10.3 How Duration Represents Test Intent

The `Duration (min)` value tells the system how long to remain in the selected recipe state. It also tells the operator why the step exists.

```text
+------------------+--------------------------------------------------------------+
| Duration Pattern | Typical Meaning                                              |
+------------------+--------------------------------------------------------------+
| 0 min            | Immediate DropView action, valve state change, or transition |
| 5 min            | Short rinse, stop window, or quick stabilization             |
| 10-15 min        | Exposure, plateau observation, trend step, or response step  |
| 20-30 min        | Replicate measurement or extended stabilization              |
| 90 min           | Warm-up or load phase                                        |
| Very long values | Long-term stability or closed-loop phase                     |
+------------------+--------------------------------------------------------------+
```

When creating or reviewing a recipe, check that each duration is long enough for the physical fluid path and the sensor response. A duration can be syntactically valid but still too short for the intended test.

### 10.4 Common Scenario Families

The scenario directory contains several recurring test families. These names may appear in recipe files and logs:

```text
+-----------------------------+---------------------------------------------------------------------+
| Scenario Family             | Test Intent                                                         |
+-----------------------------+---------------------------------------------------------------------+
| Day 1                       | Warm-up, calibration, linearity, and point accuracy                 |
| Day 2a                      | Lag and response time using step changes such as 65 -> 215 mg/dL   |
| Day 2b                      | Trend accuracy using rise, fall, and biphasic glucose profiles      |
| Day 3                       | Repeatability with repeated exposure and sensor reinsertion windows |
| Day 4                       | Reproducibility across repeated reinsertion and concentration runs  |
| Day 5+                      | Long-term stability at fixed glucose concentration                  |
| Flow Rate Comparison        | Repeated 125 mg/dL measurements at different flow rates             |
| Generic CV                  | Basic CV execution flow                                             |
| Continuous PAD Trial        | Basic continuous PAD start/stop behavior                            |
+-----------------------------+---------------------------------------------------------------------+
```

These scenario families help the operator understand why the same controls are used differently across tests. For example, a trend accuracy recipe changes Valve A ports to create a glucose profile, while a long-term stability recipe may hold one glucose concentration for a very long duration.

### 10.5 Reading Recipe Descriptions

The `Description` field is important operational metadata. It often explains the purpose of the step, such as:

1. Warm-up start or baseline verification.
2. Glucose exposure at a specific concentration.
3. PBS rinse or flush.
4. Rise, fall, or biphasic trend segment.
5. Replicate number.
6. Sensor removal and reinsertion window.
7. Manual YSI reference measurement note.
8. Measurement start, stop, or DropView exit point.

When reviewing a recipe before running it, read the descriptions from top to bottom. The hardware commands show what the software will do; the descriptions show why the step exists.

### 10.6 Valve B in Short and Long Tests

In many short glucose exposure recipes, Valve B stays in `Load` state during the measured sequence. In long stability recipes, Valve B may switch to `Inject` for closed-loop or long-duration phases.

Do not treat `Load` and `Inject` as interchangeable labels. They represent different fluidic states. If a recipe uses `Inject` for a long phase, confirm that the physical setup is prepared for that mode before starting the run.

### 10.7 Measurement Start, Stop, and DropView Restart Placement

Measurement actions are intentionally placed around the fluidic sequence. Some tests start measurement once and then change solutions while recording. Other tests periodically stop measurement, exit DropView, start DropView again, and resume measurement.

In long or repeated tests, this restart pattern is primarily a workaround for DropView acquisition timing drift. As the number of measurements increases, the actual time between acquisitions may become longer than the configured repeat/wait interval. Periodically restarting DropView helps reset the sequence so the acquisition timing returns closer to the intended interval.

Before running a recipe, confirm whether the intended output should be:

1. One continuous measurement record covering the whole fluidic profile.
2. Periodic DropView restart blocks to keep acquisition timing under control.
3. A single CV run.
4. A long-duration stability recording.

This expectation should match the selected Measurement Mode and the recipe's DropView actions.

## 11. Example Recipe Walkthroughs

This section explains how several existing recipe families should be read by an operator. These are not full SOPs. They are examples that connect recipe steps to test intent.

### 11.1 Day 1 - Warm-up, Calibration, Linearity, Point Accuracy

Purpose:

Day 1 recipes are used to warm up the sensor, expose it to known glucose concentrations, and collect data for calibration, linearity, and point accuracy evaluation.

Typical operational pattern:

1. Start DropView.
2. Start Discrete PAD measurement.
3. Hold the sensor at 125 mg/dL for warm-up and stabilization.
4. Step through glucose concentrations such as 40, 65, 125, 215, 300, and 400 mg/dL.
5. Use PBS rinse steps between selected concentration exposures.
6. Stop measurement and exit DropView.

How to read the recipe:

```text
+------------------+--------------------------------------------------------------+
| Recipe Element   | Operational Meaning                                          |
+------------------+--------------------------------------------------------------+
| Port 1 / PBS     | Rinse or stabilization between glucose exposures             |
| Port 2 / 40      | Low glucose point for accuracy and linearity                 |
| Port 3 / 65      | Low-mid glucose point                                        |
| Port 4 / 125     | Warm-up, baseline, and common reference concentration        |
| Port 5 / 215     | High glucose point                                           |
| Port 6 / 300     | Higher glucose point                                         |
| Port 7 / 400     | Highest glucose point                                        |
| 10-15 min holds  | Exposure windows used for later value extraction or review   |
| 90 min hold      | Warm-up / stabilization period                               |
+------------------+--------------------------------------------------------------+
```

Operator focus:

Confirm that Valve A tubing matches the expected concentration mapping before starting the run. During review, use the recipe descriptions and graph markers to identify which glucose concentration was active during each response region.

### 11.2 Day 2a - Lag and Response Time Test

Purpose:

Day 2a recipes are used to evaluate response timing. They intentionally switch between lower and higher glucose concentrations so the rise and fall response can be calculated after the run.

Typical operational pattern:

1. Start DropView.
2. Start measurement with the method/script timing required by the test.
3. Confirm a baseline or stable point, often around 125 mg/dL.
4. Switch between 65 mg/dL and 215 mg/dL in repeated rise/fall cycles.
5. Stop measurement after the final cycle.
6. Exit DropView.

How to read the recipe:

```text
+----------------------+--------------------------------------------------------------+
| Recipe Element       | Operational Meaning                                          |
+----------------------+--------------------------------------------------------------+
| 65 mg/dL step        | Lower level before or after a response transition            |
| 215 mg/dL step       | Higher level used for rise response                          |
| 65 -> 215 transition | Rise response; t10/t50/t90 may be calculated in post-process |
| 215 -> 65 transition | Fall response                                                |
| Repeated cycles      | Repeated evidence for response time behavior                 |
+----------------------+--------------------------------------------------------------+
```

Operator focus:

The exact switching time matters. Make sure the recipe descriptions clearly identify rise and fall transitions. In the Graphics Viewer, use response delay if valve-switch markers appear earlier than the actual sensor response.

### 11.3 Day 5+ - Long-Term Stability

Purpose:

Day 5+ recipes are used for long-duration stability testing at a fixed glucose concentration. These recipes may include very long durations and may use Valve B `Inject` for closed-loop or long-duration phases.

Typical operational pattern:

1. Start DropView.
2. Start measurement using the required long-interval script or method.
3. Confirm the baseline concentration, such as 125 mg/dL.
4. Run a load or preparation phase.
5. Enter a very long stability phase.
6. Perform manual reference checks when required by the test plan.
7. Stop measurement, rinse with PBS, and exit DropView.

How to read the recipe:

```text
+---------------------+-------------------------------------------------------------+
| Recipe Element      | Operational Meaning                                         |
+---------------------+-------------------------------------------------------------+
| 125 mg/dL hold      | Fixed glucose condition for the stability group             |
| 90 min phase        | Load, preparation, or extended stabilization                 |
| Very long duration  | Long-term stability phase, such as multi-day closed-loop    |
| Valve B = Inject    | Long-duration inject or closed-loop fluidic state           |
| PBS rinse at finish | End-of-run rinse before measurement stop or shutdown        |
+---------------------+-------------------------------------------------------------+
```

Operator focus:

Before starting a long stability run, confirm physical fluid volume, waste capacity, tubing state, pump/flow conditions, DropView timing strategy, and manual reference measurement requirements. Long-duration recipes should be reviewed more carefully than short exposure recipes because an incorrect port, valve state, or duration can affect many hours or days of data.

### 11.4 Flow Rate Comparison

Purpose:

Flow Rate Comparison recipes are used to compare sensor behavior at the same glucose concentration under different flow-rate conditions.

Typical operational pattern:

1. Start DropView.
2. Select the target concentration, commonly 125 mg/dL.
3. Start Discrete PAD measurement.
4. Hold the same concentration for a fixed time at one flow rate.
5. Stop and restart measurement for the next flow-rate condition.
6. Repeat until all flow-rate conditions are complete.
7. Exit DropView.

Operator focus:

The recipe descriptions may list flow rates such as 0.01, 0.03, 0.1, 0.3, 1.0, or 3.0 mL/min. Confirm that the physical pump/flow setting matches the recipe description and the `Flow Rate` metadata in Measurement Setup. The recipe description records intent; the operator is still responsible for making sure the physical flow condition is actually set.

## 12. Session and Package Structure

Each measurement session is stored under the selected Package Root.

Folder structure:

```text
{package_root}/
  {part_number}/
    {YYYYMMDD_HHMMSS}/
      session.json
      measurement_parameters/
        {part_number}.tp or {part_number}.tc
        {part_number}.scr
        recipe.json
      measurements/
        measurement output files
      logs/
        session log files
```

```text
+------------------------+---------------------------------------------------------------------------------------------+
| Item                   | Description                                                                                 |
+------------------------+---------------------------------------------------------------------------------------------+
| session.json           | Stores session metadata, status, files, notes, experiment parameters, and measurement mode. |
| measurement_parameters | Stores copied or generated method, script, and recipe files used for the session.           |
| measurements           | Stores CSV, PAD, CV, or generated measurement output files.                                 |
| logs                   | Stores session-specific log files.                                                          |
+------------------------+---------------------------------------------------------------------------------------------+
```

Session statuses include:

```text
+-------------+---------------------------------------------------------+
| Status      | Meaning                                                 |
+-------------+---------------------------------------------------------+
| pending     | Session was created but the recipe has not started yet. |
| in_progress | Recipe is running.                                      |
| completed   | Recipe completed successfully.                          |
| aborted     | Operator stopped the recipe.                            |
| error       | Recipe or measurement ended with an error.              |
+-------------+---------------------------------------------------------+
```

## 13. Recipe Files and Recipe Steps

Recipe files define automated sequences for valves, pump, DropView actions, and timed waits.

Each recipe step contains:

```text
+------------------+----------------------------------------------------------------------------------------------+
| Field            | Meaning                                                                                      |
+------------------+----------------------------------------------------------------------------------------------+
| port             | Valve A command. `0` means no change; `1` to `8` select the corresponding port.              |
| valve_b_state    | Valve B command. `0` means no change, `1` means Load, and `2` means Inject.                  |
| pump_rpm         | Pump command. Empty means no change, `0` stops the pump, and a positive number runs forward. |
| dropview_action  | DropView action key such as start, stop, continuous PAD, CV, or exit.                        |
| duration_minutes | Step wait duration in minutes.                                                               |
| description      | Free-text description for operator clarity and logs.                                         |
+------------------+----------------------------------------------------------------------------------------------+
```

Recipe loops repeat the full recipe. Step loops repeat a selected range of steps within the recipe.

## 14. Data Outputs and Storage Folders

The application can produce or collect several output types depending on measurement mode:

```text
+------------------+-----------------------------+-------------------------------------------------------+
| Output           | Typical Source              | Location                                              |
+------------------+-----------------------------+-------------------------------------------------------+
| CSV files        | Discrete PAD script export  | `measurements/`                                       |
| xlsx aggregate   | Aggregator output           | Session folder or measurement package output location |
| `.mtp` files     | DropView AutoSave for PAD   | `measurements/`                                       |
| `.mtc` files     | DropView AutoSave for CV    | `measurements/`                                       |
| session log      | Application session logging | `logs/`                                               |
| session metadata | Application session state   | `session.json`                                        |
+------------------+-----------------------------+-------------------------------------------------------+
```

Use `Aggregate CSVs -> xlsx` when CSV data needs to be merged manually. Final aggregation also runs automatically when recipe execution completes.

## 15. Status Messages, Logs, and Warnings

The application uses several places to communicate status:

```text
+-------------------------------+------------------------------------------------------------------+
| Location                      | Use                                                              |
+-------------------------------+------------------------------------------------------------------+
| Device status labels          | Show connection or current known device state in Manual Control. |
| Package Status                | Shows measurement package and session status.                    |
| Recipe Execution status       | Shows current recipe runner state.                               |
| Progress bar                  | Shows timed step progress.                                       |
| Package Log                   | Shows session packaging and aggregation messages.                |
| Full Log                      | Shows broader application messages.                              |
| Graphics Viewer System Events | Shows major run events aligned with measurement review.          |
+-------------------------------+------------------------------------------------------------------+
```

Warnings and errors should not be ignored. If a device does not connect, DropView cannot start, or a method file is incompatible with the selected measurement mode, correct the issue before running a formal test.

## 16. Safe Operation Notes

Before starting a recipe:

1. Confirm all required hardware is physically connected.
2. Confirm the correct COM ports are selected.
3. Confirm valves are safe to move to the requested positions.
4. Confirm pump direction and target RPM are appropriate for the fluid path.
5. Confirm DropView is installed and can be launched.
6. Confirm the correct measurement mode and method file are selected.
7. Confirm the Package Root points to the intended data storage location.
8. Confirm the recipe contains the intended DropView start, measurement, stop, and exit actions.

During execution:

1. Monitor Recipe Execution status and progress.
2. Watch the Package Log and Full Log for errors.
3. Use Pause only when it is safe to temporarily hold the process.
4. Use Stop Recipe when the run should be ended in a controlled way.
5. Use Emergency Stop ALL when immediate hardware stop is required.

## 17. Common Issues and Operator Checklist

### 15.1 Common Issues

```text
+---------------------------+--------------------------------------------------------------------+------------------------------------------------------------+
| Issue                     | Possible Cause                                                     | Suggested Action                                           |
+---------------------------+--------------------------------------------------------------------+------------------------------------------------------------+
| COM port is missing       | Device not connected, driver issue, or port list is stale.         | Connect the device and press Refresh COM Ports.            |
| Valve test fails          | Wrong COM port, baud rate, address, or device power state.         | Check cabling, power, COM port, baud, and address.         |
| Pump commands do not work | Pump controller not connected or wrong COM port selected.          | Connect Pump and query Status.                             |
| DropView does not start   | DropView is not installed, not found, or blocked by Windows state. | Start DropView manually if needed and retry.               |
| DropSens connection fails | Wrong COM port or DropView connection state mismatch.              | Select the correct COM port and retry Connect.             |
| Method file rejected      | Measurement mode and method type do not match.                     | Select the correct mode before loading `.tp` or `.tc`.     |
| CSV path is empty         | Part Number or Package Root is missing.                            | Enter Part Number and select Package Root.                 |
| Recipe cannot start       | Required package inputs are incomplete.                            | Review Measurement Setup and recipe file references.       |
| No live graph appears     | No active session, unsupported file, or data not yet written.      | Load Active Session, Refresh, or check measurement output. |
+---------------------------+--------------------------------------------------------------------+------------------------------------------------------------+
```

### 15.2 Pre-Run Checklist

Use this checklist before a standard test:

```text
+------------------------------------------+------+
| Check                                    | Done |
+------------------------------------------+------+
| Valve A connected and tested.            |      |
| Valve B connected and tested.            |      |
| Pump connected and status checked.       |      |
| DropView started and DropSens connected. |      |
| Correct Part Number entered.             |      |
| Correct Package Root selected.           |      |
| Correct Measurement Mode selected.       |      |
| Correct method file loaded.              |      |
| Experiment Conditions reviewed.          |      |
| Port - Glucose Mapping reviewed.         |      |
| Recipe loaded or created.                |      |
| Recipe duration and loops reviewed.      |      |
| Output/session location confirmed.       |      |
+------------------------------------------+------+
```

## 18. Screenshot List for Visual Version

The following screenshots should be added in the next documentation phase:

```text
+-----------------------------------------+--------------------------------------------------------------+
| Placeholder                             | Suggested Screenshot                                         |
+-----------------------------------------+--------------------------------------------------------------+
| Main application window                 | Full application with tab bar visible.                       |
| Manual Control tab                      | Full hardware control page with all device sections visible. |
| Measurement Setup tab                   | Full three-column setup page.                                |
| Method editor panel                     | Loaded method file showing parameters.                       |
| Recipe Control tab                      | Recipe with several example steps loaded.                    |
| Graphics Viewer tab with loaded session | Graphs, annotation panel, and toolbar visible.               |
| Full Log tab                            | Log area with example timestamp and messages.                |
+-----------------------------------------+--------------------------------------------------------------+
```

