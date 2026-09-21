# SharpCap Focus Sequencer

On-demand thermal focus compensator for ZWO EAF focusers via ASCOM.

The sequencer reads a temperature regression model and the latest autofocus
reference produced by
[sharpcap-focus-temperature](https://github.com/davidglt/sharpcap-focus-temperature),
reads the current EAF external-sensor temperature, calculates the target focus
position, and moves the selected focuser through ASCOM.

The project supports two independent optical trains:

| Tube | Optical train | Focuser access | State JSON | Wrapper |
|---|---|---|---|---|
| Main | Celestron C8 + f/6.3 reducer + ASI2600MC Pro | ZWO EAF through ASCOM Device Hub | `..\sharpcap-focus-temperature\sharpcap_focus_state.json` | `run_focus.bat` |
| Guide | Sky-Watcher 50ED + ASI224MC | Second ZWO EAF through direct ASCOM access | `..\sharpcap-focus-temperature\sharpcap_focus_state_guide.json` | `run_focus_guide.bat` |

The main and guide tubes have independent thermal models. Configurable filter
offsets apply only to the main C8 imaging train.

## Thermal focus model

For both optical tubes, the base thermal position is calculated from the
reference autofocus point:

```text
base_focus_target = focus_ref + TCF × (T_current - T_ref)
```

| Variable | Description |
|---|---|
| `focus_ref` | Focuser position at the reference autofocus point |
| `T_ref` | Temperature at the reference autofocus point |
| `T_current` | Current temperature measured by the EAF external sensor |
| `TCF` | Thermal compensation factor in EAF steps per °C |
| `base_focus_target` | Temperature-predicted focus position before a filter offset |

The main-tube model must be calibrated with filter position 1, `No filter`.

For the main tube, the final target includes the selected filter offset:

```text
final_focus_target = base_focus_target + filter_offset_steps
```

For the guide tube, no filter offset exists:

```text
final_focus_target = base_focus_target
```

The final target is clamped to the available ASCOM focuser limits before any
movement is commanded.

## Main-tube filter offsets

Main-tube filter positions are configured in the local
`focus_sequencer.properties` file.

Initial configuration:

| Position | Filter | Offset |
|---:|---|---:|
| 1 | No filter | 0 steps |
| 2 | Optolong L-eNhance Nebular | +500 steps |
| 3–7 | Reserved | 0 steps until calibrated |

The sign convention is:

- A positive offset increases the commanded EAF position.
- A negative offset decreases the commanded EAF position.

Example with filter position 2:

```text
base_focus_target = 18500
filter_offset_steps = +500
final_focus_target = 19000
```

### Filter-offset calibration

Calibrate each main-tube filter against filter position 1:

1. Select filter position 1, `No filter`.
2. Run a SharpCap autofocus and record the focus position.
3. Select the filter to calibrate.
4. Run a second SharpCap autofocus.
5. Calculate the filter offset:

   ```text
   filter_offset_steps = filtered_focus_position - no_filter_focus_position
   ```

6. Save the value in `filter.N.offset_steps`.
7. Repeat the comparison over multiple nights and temperatures to confirm that
   the offset is stable.

Do not mix autofocus measurements made with filters into the no-filter thermal
model. If an offset later proves temperature-dependent, model that correction
separately rather than contaminating the common base regression.

## Configuration

Copy the template before the first main-tube execution:

```powershell
Copy-Item .\focus_sequencer.properties.example .\focus_sequencer.properties
```

`focus_sequencer.properties` is ignored by Git because it may contain
observatory-specific offsets. The `.example` file is the versioned template.

Initial configuration:

```properties
filter.default = 1

filter.1.name = No filter
filter.1.offset_steps = 0

filter.2.name = Optolong L-eNhance Nebular
filter.2.offset_steps = 500
```

Configuration rules:

- `filter.default` is used when `--filter` is omitted.
- Valid main-tube filter positions are 1 through 7.
- Filter position 1 is the thermal-model reference and must retain offset `0`.
- `--filter` and `--config` apply only to the main tube.
- The guide tube does not load a properties file and always uses offset `0`.

## Two-repository layout

Clone both projects as sibling directories. The parent folder may have any name.

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
    ├── run_focus_guide.bat
    └── detect_focusers.py
```

Do not copy state JSON files into this repository. They are generated and
maintained by `sharpcap_focuser.py` in the sibling
`sharpcap-focus-temperature` repository.

## Focus cycle

Each non-dry-run execution performs the following operations:

1. Resolves the state JSON and identifies the tube.
2. Loads the main-tube filter configuration, or uses a fixed zero offset for
   the guide tube.
3. Loads the previous thermal focus state.
4. Connects to the selected ASCOM focuser.
5. Stops without moving if `IsMoving=True`, avoiding interference with a
   SharpCap autofocus operation.
6. Refreshes the state JSON from the latest SharpCap autofocus logs.
7. Reads the current EAF position and temperature.
8. Calculates the base thermal target.
9. Applies the selected main-tube filter offset, if applicable.
10. Clamps the target to the ASCOM focuser limits.
11. Moves with backlash compensation when required.
12. Saves the latest applied temperature and final target position to the
    state JSON.

In `--dry-run` mode, the sequencer does not refresh the state JSON and does
not move the EAF.

## Installation

Clone both repositories:

```powershell
cd C:\astro
git clone [https://github.com/davidglt/sharpcap-focus-sequencer.git](https://github.com/davidglt/sharpcap-focus-sequencer.git)
git clone [https://github.com/davidglt/sharpcap-focus-temperature.git](https://github.com/davidglt/sharpcap-focus-temperature.git)
```

Create and install each virtual environment:

```powershell
cd C:\astro\sharpcap-focus-sequencer
python -m venv .venv
.\.venv\Scripts\pip install -r .\requirements\requirements.txt

cd C:\astro\sharpcap-focus-temperature
python -m venv .venv
.\.venv\Scripts\pip install -r .\requirements\requirements.txt
```

Create the local main-tube filter configuration:

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
- A sibling `sharpcap-focus-temperature` clone with its virtual environment
  and state JSON files

Install the Python dependency:

```powershell
pip install pywin32
```

## ASCOM access

### Main tube

SharpCap and the sequencer may both need simultaneous access to the main ZWO
EAF. Use **ASCOM Device Hub** as a shared proxy:

```text
ASCOM.DeviceHub.Focuser
```

Configure Device Hub to proxy the main ZWO EAF driver, usually
`ASCOM.EAF.Focuser`, then configure both SharpCap and the sequencer to use
Device Hub.

### Guide tube

If SharpCap does not access the guide EAF, the guide tube can use direct ASCOM
access:

```text
ASCOM.EAF_2.Focuser
```

ZWO ASCOM ProgIDs can change when USB topology changes. Run
`detect_focusers.py` after changing hubs, adapters, cabling, device power order
or camera connections.

## Usage

### Main tube

Default execution uses the configured `filter.default`, initially position 1:

```powershell
run_focus.bat
```

Explicit `No filter` operation:

```powershell
run_focus.bat --filter 1
```

Optolong L-eNhance Nebular:

```powershell
run_focus.bat --filter 2
```

Dry-run with the L-eNhance filter:

```powershell
run_focus.bat --filter 2 --dry-run
```

Dry-run with a simulated temperature:

```powershell
run_focus.bat --filter 2 --dry-run --temp 18.5
```

Use a custom main-tube properties file:

```powershell
run_focus.bat --config .\custom_focus_sequencer.properties --filter 2
```

### Guide tube

The guide tube always uses a zero filter offset:

```powershell
run_focus_guide.bat
run_focus_guide.bat --dry-run
```

Do not pass `--filter` or `--config` to `run_focus_guide.bat`. The script
returns an error instead of accepting a filter-selection request for the guide
tube.

### Direct execution

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
| `--state-json PATH` | Main state JSON in sibling repository | State JSON produced by `sharpcap_focuser.py`; a filename containing `guide` selects guide-tube behavior |
| `--ascom-id PROGID` | `ASCOM.DeviceHub.Focuser` | ASCOM focuser ProgID |
| `--config PATH` | `focus_sequencer.properties` beside the script | Main-tube properties file; rejected for the guide tube |
| `--filter`, `-f` | `filter.default` | Main-tube filter position from 1 through 7; rejected for the guide tube |
| `--dry-run` | Off | Reads state, focuser position and temperature but does not refresh state or move the EAF |
| `--temp DEGREES` | EAF sensor reading | Overrides sensor temperature, mainly for dry-run testing |
| `--backlash STEPS` | `500` | Backlash compensation in EAF steps; `0` disables it |
| `--min-correction STEPS` | `50` | Minimum correction used only when backlash overshoot is required |
| `--move-timeout SECONDS` | `60` | Maximum wait time for each focuser movement |

## Backlash compensation

The sequencer approaches a target from below whenever backlash compensation is
needed. For the C8 main tube, increasing EAF step numbers correspond to an
outward movement.

If the target is below the current position:

1. Move to `target - backlash`.
2. Move outward to `target`.

Use only one backlash-compensation layer:

| Layer | Recommended setting |
|---|---|
| ZWO EAF ASCOM driver | `0` |
| SharpCap backlash | `0` |
| `--backlash` in this sequencer | `500` initially, then refine from measurement |

The ZWO EAF driver reports commanded position, not a direct physical optical
measurement. Determine backlash with an optical method, such as SharpCap
double V-curves approached from opposite directions.

## Logging

Logs are written to:

```text
logs\YYYYMMDD_focus_sequencer.log
```

Example main-tube L-eNhance correction:

```text
2026-09-21 23:15:02 | INFO  | tube=main | T=12.30°C | dT=-1.10°C |
TCF=-61.59 | filter=2 (Optolong L-eNhance Nebular) |
filter_offset=+500 | base_target=18500 | target=19000 | pos=18920 |
correction=+80 | backlash=False | final=19000
```

Example guide-tube correction:

```text
tube=guide | filter=N/A | filter_offset=+0 |
base_target=345000 | target=345000
```

## Typical nightly workflow

1. Confirm that both EAF units are connected and identified correctly.
2. Set the C8 filter wheel to position 1, `No filter`.
3. Run SharpCap autofocus for the main tube.
4. Select the desired capture filter.
5. Start imaging.
6. Run `run_focus.bat --filter 2` periodically while capturing with the
   Optolong L-eNhance Nebular filter.
7. If SharpCap performs a full autofocus because of a temperature change, the
   next sequencer cycle refreshes the state JSON and adopts the new reference.
8. Run `run_focus_guide.bat` independently for guide-tube thermal correction;
   it never applies a filter offset.

## Multiple EAF units

With multiple ZWO EAF units, driver ProgIDs are assigned by USB enumeration
order. Verify them after hardware changes:

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

The guide EAF must have its correct maximum travel configured in ASICap. After
a firmware update, reset or accidental position-counter change, restore its
coordinate system before allowing automated movement.

## Related project

- [sharpcap-focus-temperature](https://github.com/davidglt/sharpcap-focus-temperature)
  extracts autofocus data from SharpCap logs, fits the temperature regression
  and produces the state JSON consumed by this sequencer.

## License

This project is licensed under the **GNU General Public License v3.0 or later**.
See `LICENSE.txt` for the complete license text.

## Author

David González López-Tercero  
[https://dragonit.es](https://dragonit.es)  
[davidglt@dragonit.es](mailto:davidglt@dragonit.es)