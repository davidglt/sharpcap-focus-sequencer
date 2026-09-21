# SharpCap Focus Sequencer

On-demand thermal focus compensator for ZWO EAF focusers via ASCOM. It reads
the regression model and the last autofocus reference produced by
[sharpcap-focus-temperature](https://github.com/davidglt/sharpcap-focus-temperature),
reads the current EAF external-sensor temperature, and moves the focuser to the
thermally compensated position.

The project supports two independent optical trains:

| Tube | Imaging train | Focuser | State JSON | Entry point |
|---|---|---|---|---|
| Main | Celestron C8 + f/6.3 reducer + ASI2600MC Pro | Main ZWO EAF via ASCOM Device Hub | `..\sharpcap-focus-temperature\sharpcap_focus_state.json` | `run_focus.bat` |
| Guide | Sky-Watcher 50ED + ASI224MC | Second ZWO EAF, direct ASCOM | `..\sharpcap-focus-temperature\sharpcap_focus_state_guide.json` | `run_focus_guide.bat` |

The main and guide tubes use separate thermal models. Filter focus offsets apply
only to the main imaging tube.

## How it works

For either tube, the sequencer reads the reference autofocus position and the
thermal compensation factor from the state JSON:

```text
base_focus_target = focus_ref + TCF × (T_current - T_ref)
```

| Variable | Description |
|---|---|
| `focus_ref` | Focuser position at the reference autofocus point |
| `T_ref` | Temperature at the reference autofocus point |
| `T_current` | Current temperature from the EAF external sensor |
| `TCF` | Temperature compensation factor in EAF steps per °C |
| `base_focus_target` | Temperature-predicted focus position before a filter offset |

For the main tube, the thermal model is always calibrated with filter position
1, **Sin filtro**. If an imaging filter is selected, its configured offset is
added after calculating the no-filter thermal position:

```text
final_target = base_focus_target + filter_offset_steps
```

For the guide tube, no filter offset is applied:

```text
final_target = base_focus_target
```

The final target is constrained to the ASCOM focuser limits before it is sent
to the EAF.

## Main-tube filter offsets

The main tube can use filter positions 1 to 7. Filter data is loaded from the
local `focus_sequencer.properties` file.

The initial configuration is:

| Position | Filter | Offset |
|---:|---|---:|
| 1 | Sin filtro | 0 steps |
| 2 | Optolong L-eNhance Nebular | +500 steps |
| 3–7 | Reserved | 0 steps until calibrated |

The sign convention is:

- Positive offset: increases the commanded EAF position
- Negative offset: decreases the commanded EAF position

For example, if the no-filter thermal model predicts `18,500` steps, selecting
the Optolong L-eNhance produces:

```text
base_focus_target = 18500
filter_offset_steps = +500
final_target = 19000
```

### Calibrating filter offsets

Use the following workflow for each imaging filter:

1. Select position 1, **Sin filtro**
2. Run SharpCap autofocus and record the focus position
3. Insert or select the target filter
4. Run SharpCap autofocus again
5. Calculate:

   ```text
   filter_offset_steps = filtered_focus_position - no_filter_focus_position
   ```

6. Update `filter.N.offset_steps` in `focus_sequencer.properties`
7. Repeat at several temperatures to confirm that the offset remains stable

The model should only be trained with autofocus samples made without a filter.
If the offset proves temperature-dependent, do not mix those measurements into
the base model; calibrate a future per-filter temperature correction instead.

## Configuration

Copy the versioned template before first use:

```powershell
Copy-Item .\focus_sequencer.properties.example .\focus_sequencer.properties
```

`focus_sequencer.properties` is ignored by Git because it contains
observatory-specific offsets. The template is committed to the repository.

Example configuration:

```properties
filter.default = 1

filter.1.name = Sin filtro
filter.1.offset_steps = 0

filter.2.name = Optolong L-eNhance Nebular
filter.2.offset_steps = 500
```

- `filter.default` is used when `--filter` is omitted.
- Valid filter positions are 1 to 7.
- Filter 1 is the model reference and must retain offset `0`.
- Filters are used only for the main tube.
- `run_focus_guide.bat` does not use this file and always applies an offset of
  `0`.

## Two-repository layout

Both repositories must be cloned as sibling directories. The exact parent path
does not matter.

```text
<any-parent>\
├── sharpcap-focus-temperature\
│   ├── sharpcap_focuser.py
│   ├── sharpcap_focus_state.json
│   └── sharpcap_focus_state_guide.json
└── sharpcap-focus-sequencer\
    ├── focus_sequencer.py
    ├── focus_sequencer.properties.example
    ├── focus_sequencer.properties
    ├── run_focus.bat
    └── run_focus_guide.bat
```

Do not copy the state JSON files into this repository. They are generated and
maintained by `sharpcap_focuser.py` in the sibling
`sharpcap-focus-temperature` repository.

## Focus cycle

Each non-dry-run execution follows this sequence:

1. Loads the filter configuration for the main tube, or uses a zero offset for
   the guide tube
2. Loads the previous focus state from the corresponding sibling-repository
   JSON file
3. Connects to the requested ASCOM focuser
4. Stops without moving if `IsMoving=True`, so it never interferes with a
   SharpCap autofocus operation
5. Refreshes the state JSON from the latest SharpCap autofocus logs
6. Reads the EAF temperature and current focuser position
7. Calculates the thermal target
8. Adds the selected main-tube filter offset, if applicable
9. Applies ASCOM position limits
10. Moves with backlash compensation when required
11. Stores the last temperature and final physical target in the state JSON

Dry-run mode does not refresh the state JSON and never moves the EAF.

## Installation

Clone both repositories:

```powershell
cd C:\astro
git clone [https://github.com/davidglt/sharpcap-focus-sequencer.git](https://github.com/davidglt/sharpcap-focus-sequencer.git)
git clone [https://github.com/davidglt/sharpcap-focus-temperature.git](https://github.com/davidglt/sharpcap-focus-temperature.git)
```

Create a virtual environment for each repository:

```powershell
cd C:\astro\sharpcap-focus-sequencer
python -m venv .venv
.\.venv\Scripts\pip install -r .\requirements\requirements.txt

cd C:\astro\sharpcap-focus-temperature
python -m venv .venv
.\.venv\Scripts\pip install -r .\requirements\requirements.txt
```

Create the local main-tube configuration:

```powershell
cd C:\astro\sharpcap-focus-sequencer
Copy-Item .\focus_sequencer.properties.example .\focus_sequencer.properties
```

## Requirements

- Windows 10 or Windows 11
- Python 3.10 or later
- [ASCOM Platform](https://ascom-standards.org/)
- ZWO EAF ASCOM driver
- `pywin32`
- A sibling `sharpcap-focus-temperature` installation with an available state
  JSON and virtual environment

Install the Python dependency:

```powershell
pip install pywin32
```

## ASCOM access

### Main tube

SharpCap and the script can require simultaneous access to the main ZWO EAF.
Use **ASCOM Device Hub** as the shared proxy:

```text
ASCOM.DeviceHub.Focuser
```

Configure Device Hub to proxy the main ZWO EAF driver, usually
`ASCOM.EAF.Focuser`, and configure SharpCap and the sequencer to use Device
Hub.

### Guide tube

SharpCap does not access the guide-tube EAF, so it may be accessed directly:

```text
ASCOM.EAF_2.Focuser
```

The exact ZWO ASCOM ProgID can change when USB device enumeration changes.
Use `detect_focusers.py` after modifying USB hubs, adapters, cabling or power
order.

## Usage

### Main tube

Normal execution; filter 1, **Sin filtro**, is used by default:

```powershell
run_focus.bat
```

Explicit no-filter operation:

```powershell
run_focus.bat --filter 1
```

Optolong L-eNhance Nebular:

```powershell
run_focus.bat --filter 2
```

Dry-run with the Optolong filter:

```powershell
run_focus.bat --filter 2 --dry-run
```

Dry-run at an explicitly simulated temperature:

```powershell
run_focus.bat --filter 2 --dry-run --temp 18.5
```

### Guide tube

The guide tube never uses a filter offset:

```powershell
run_focus_guide.bat
run_focus_guide.bat --dry-run
```

Do not pass `--filter` to `run_focus_guide.bat`. Filter offsets are exclusive
to the C8 main imaging train.

### Direct Python execution

Main tube:

```powershell
python .\focus_sequencer.py
python .\focus_sequencer.py --filter 2
python .\focus_sequencer.py --config .\focus_sequencer.properties --filter 2
```

Guide tube:

```powershell
python .\focus_sequencer.py `
  --ascom-id "ASCOM.EAF_2.Focuser" `
  --state-json "..\sharpcap-focus-temperature\sharpcap_focus_state_guide.json"
```

## Command-line options

| Option | Default | Description |
|---|---|---|
| `--state-json PATH` | Auto-detected main state file | State JSON produced by `sharpcap_focuser.py`; use a guide-state path for the guide tube |
| `--ascom-id PROGID` | `ASCOM.DeviceHub.Focuser` | ASCOM focuser ProgID |
| `--config PATH` | `focus_sequencer.properties` beside the script | Main-tube filter configuration file |
| `--filter`, `-f` | `filter.default` | Main-tube target filter position from 1 to 7 |
| `--dry-run` | Off | Reads state, position and temperature but does not refresh state or move the EAF |
| `--temp DEGREES` | EAF sensor reading | Temperature override, useful for dry-run testing |
| `--backlash STEPS` | `500` | Backlash compensation. Use `0` to disable |
| `--min-correction STEPS` | `50` | Minimum correction applied only when a backlash overshoot is required |
| `--move-timeout SECONDS` | `60` | Timeout for each EAF move |

## Backlash compensation

The sequencer always approaches a target from below when backlash compensation
is active. With the C8 setup, increasing focuser step numbers correspond to an
outward movement.

If the desired target is below the current position:

1. Move to `target - backlash`
2. Move outward to `target`

This ensures a repeatable final approach direction.

Use only one layer of backlash compensation:

| Layer | Recommended setting |
|---|---|
| ZWO EAF ASCOM driver | `0` |
| SharpCap backlash | `0` |
| `--backlash` in this sequencer | `500` initially; refine after measurement |

The EAF driver reports commanded position rather than a physical encoder
measurement. Determine backlash with an optical method, such as SharpCap
double V-curves approached from opposite directions.

## Logging

Logs are written to:

```text
logs\YYYYMMDD_focus_sequencer.log
```

A main-tube L-eNhance correction can look like:

```text
2026-09-21 23:15:02 | INFO  | T=12.30°C | dT=-1.10°C | TCF=-61.59 |
filter=2 (Optolong L-eNhance Nebular) | filter_offset=+500 |
base_target=18500 | target=19000 | pos=18920 | correction=+80 |
backlash=False | final=19000
```

For the guide tube, the log records a zero offset:

```text
tube=guide | filter=N/A | filter_offset=+0 | base_target=345000 | target=345000
```

## Typical nightly workflow

1. Start the imaging session and ensure both EAF units are connected.
2. With the C8 in filter position 1, **Sin filtro**, run SharpCap autofocus.
3. Change the main imaging train to the desired capture filter.
4. Start capture.
5. Run `run_focus.bat --filter 2` periodically for L-eNhance imaging, for
   example after a dither block or at a scheduled interval.
6. SharpCap may perform a full autofocus after its configured temperature
   change. The next sequencer cycle refreshes the state JSON and adopts the
   updated reference automatically.
7. Run `run_focus_guide.bat` independently for the guide tube if needed; it
   uses no filter offset.

## Multiple EAF units

With two ZWO EAF focusers, ASCOM ProgIDs are assigned by the ZWO driver in USB
enumeration order. Verify each device after hardware changes:

```powershell
python .\detect_focusers.py
```

Typical output:

```text
[OK] ASCOM.EAF.Focuser
     Position    : 18,700 steps
     Temperature : 18.40°C

[OK] ASCOM.EAF_2.Focuser
     Position    : 345,000 steps
     Temperature : 18.10°C
```

The guide EAF must have the correct maximum travel configured in ASICap. After
a firmware update, reset or accidental position-counter change, restore its
coordinate system before allowing automated movement.

## Related project

- [sharpcap-focus-temperature](https://github.com/davidglt/sharpcap-focus-temperature):
  extracts SharpCap autofocus data, filters valid samples and produces the
  temperature regression and state JSON consumed by this sequencer.

## License

This project is licensed under the **GNU General Public License v3.0 or later**.
See `LICENSE.txt`.

## Author

David González López-Tercero  
[https://dragonit.es](https://dragonit.es)  
[davidglt@dragonit.es](mailto:davidglt@dragonit.es)