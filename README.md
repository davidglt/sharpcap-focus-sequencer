# SharpCap Focus Sequencer

On-demand thermal focus compensator for the ZWO EAF focuser via ASCOM.
Reads the regression model and last autofocus reference produced by
[sharpcap-focus-temperature](https://github.com/davidglt/sharpcap-focus-temperature)
and moves the focuser to the thermally corrected position.

Designed to be called from a nightly sequencer (NINA, SGP'Pro, etc.)
after dither operations.

## Thermal compensation formula

```text
focus_target = focus_ref + TCF × (T_current − T_ref)
```

| Variable | Description |
|---|---|
| `focus_ref` | Focuser position at the reference autofocus point |
| `T_ref` | Temperature at the reference autofocus point |
| `T_current` | Current temperature read from the EAF external sensor |
| `TCF` | Temperature compensation factor (steps/°C) = 1/k from the regression |

## How it works

1. Reads `sharpcap_focus_state.json` (produced by `sharpcap_focuser.py`).
2. Connects to the ZWO EAF via ASCOM and reads the external temperature sensor.
3. Calculates the thermally compensated target position.
4. Moves the focuser to that position.
5. Updates `last_temp_applied` and `last_focus_applied` in the JSON state file.

## Requirements

- Python 3.10 or newer.
- Windows (ASCOM platform required).
- [ASCOM Platform](https://ascom-standards.org/) installed.
- ZWO EAF ASCOM driver installed.
- `pywin32`

```bash
pip install pywin32
```

## Usage

Normal run (connects to EAF, reads temperature, moves focuser):

```bash
python focus_sequencer.py
```

Specify a custom path to the state JSON:

```bash
python focus_sequencer.py --state-json C:\path\to\sharpcap_focus_state.json
```

Specify a custom ASCOM driver ID:

```bash
python focus_sequencer.py --ascom-id "ASCOM.ZWO.Focuser1"
```

Dry run (calculates and prints the target position without moving the focuser):

```bash
python focus_sequencer.py --dry-run
```

## Command-line options

| Option | Default | Description |
|---|---|---|
| `--state-json` | `sharpcap_focus_state.json` | Path to the JSON state file produced by `sharpcap_focuser.py`. |
| `--ascom-id` | `ASCOM.ZWO.Focuser` | ASCOM ProgID of the focuser driver. |
| `--dry-run` | off | Calculate target position without moving the focuser. |
| `--move-timeout` | `60` | Seconds to wait for the focuser move to complete. |

## Multiple EAF units

If you have more than one ZWO EAF connected (e.g. main tube + guide tube),
the ZWO ASCOM driver registers each unit under a different ProgID:

| ProgID | Typical use | Position range |
|---|---|---|
| `ASCOM.ZWO.Focuser` | First EAF (e.g. guide tube) | ~300 000 steps |
| `ASCOM.ZWO.Focuser1` | Second EAF (e.g. main tube, `EAF(ASI2600)`) | ~25 000 steps |

To identify which ProgID corresponds to which unit, run the detection utility
with both EAF units connected:

```bash
python detect_focusers.py
```

This probes all known ZWO ProgIDs and prints the `Name`, `Position`, and
`Temperature` of each focuser that responds. The main tube focuser
(C8 + ASI2600MC Pro) will show a position around **25 000 steps**.

Once identified, pass the correct ProgID to `focus_sequencer.py`:

```bash
python focus_sequencer.py --ascom-id "ASCOM.ZWO.Focuser1"
```

To make it permanent, update `DEFAULT_ASCOM_ID` at the top of `focus_sequencer.py`.

## Typical workflow

1. Run `sharpcap_focuser.py` after each imaging session to update the thermal model.
2. Run `detect_focusers.py` once (with both EAFs connected) to identify the correct ProgID.
3. In your nightly sequencer (NINA, SGP'Pro), add a **Script** step after each dither block.
4. Point that script step to `focus_sequencer.py --ascom-id <ProgID>`.
5. The script reads the current EAF temperature, calculates the correction, and moves the focuser automatically.

## State JSON

This script reads and updates `sharpcap_focus_state.json`:

```json
{
  "timestamp_ref": "2026-08-24 23:11:32",
  "temp_ref": 18.4,
  "focus_ref": 25342,
  "last_temp_applied": 17.1,
  "last_focus_applied": 25422,
  "model_tcf": -61.59,
  "model_inv_tcf": -0.016237,
  "model_intercept_c": 432.939
}
```

After each run, `last_temp_applied` and `last_focus_applied` are updated to
reflect the correction just applied, while `focus_ref` and `temp_ref` remain
unchanged as the original reference point.

## Related

- [sharpcap-focus-temperature](https://github.com/davidglt/sharpcap-focus-temperature) — extracts autofocus data from SharpCap logs and fits the thermal regression model.

## License

This project is licensed under the **GNU General Public License v3.0 or later**.

See the `LICENSE` file for the full license text.

## Author

**David González López-Tercero**  
Website: [https://dragonit.es](https://dragonit.es)  
Email: [davidglt@dragonit.es](mailto:davidglt@dragonit.es)
