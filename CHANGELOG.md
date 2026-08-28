# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- `refresh_state_json()` was invoking `sharpcap_focuser.py` with `sys.executable`,
  i.e. the Python interpreter from the sequencer's own `.venv`.  That environment
  only contains `pywin32` and lacks `numpy`, `statsmodels`, and `matplotlib`, which
  `sharpcap_focuser.py` requires.  The call now uses the Python interpreter from the
  sibling repository's `.venv` (resolved by the new `resolve_producer_python()`).

### Added

- `resolve_producer_python()`: locates the Python interpreter inside the
  `sharpcap-focus-temperature` sibling repository's `.venv`:
    - Windows: `.venv/Scripts/python.exe`
    - POSIX / WSL: `.venv/bin/python`
  Falls back to `sys.executable` with a `SKIP` warning when neither candidate
  exists (e.g. the sibling `.venv` has not been created yet).
- `--min-correction` option: minimum correction in steps required to trigger a move
  when a backlash overshoot would be needed (target < current position). Default: 50 steps
  (~0.81 °C with TCF = −61.59 steps/°C). Set to `0` to always move in both directions.
- `refresh_state_json()`: at the start of each correction cycle (after the busy check),
  `focus_sequencer.py` calls `sharpcap_focuser.py` via subprocess to regenerate
  `sharpcap_focus_state.json` from the latest SharpCap log files. This ensures that any
  autofocus triggered by SharpCap during the session (e.g. `PERIODIC Refocus WHEN TEMP
  CHANGES BY 1`) is immediately reflected in the thermal model before the next correction.
  If the refresh fails, the previously loaded state is used and the error is logged.
  Refresh is skipped in `--dry-run` mode.

### Changed

- Busy detection: the script now checks `IsMoving` immediately after connecting and
  aborts cleanly if the focuser is already moving (e.g. SharpCap autofocus is running).
  The next scheduled cycle retries automatically.
- Minimum correction threshold (`--min-correction`) now applies **only** when a backlash
  overshoot is needed (target < current position). Moves in the favourable direction
  (target ≥ current, no overshoot) are always applied regardless of correction size,
  keeping focus continuously corrected with small frequent adjustments.
- Default `--min-correction` raised from 20 to 50 steps (~0.81 °C) to better match the
  cost of a backlash overshoot cycle.
- `--dry-run` now connects to the ASCOM driver and reads the real focuser `Position`.
  The temperature is read from the EAF sensor unless `--temp` is supplied, in which case
  the supplied value is used instead. The move, state JSON refresh, and state JSON update
  are all skipped.
- `STATE_JSON_PATH` is now a single canonical constant pointing exclusively to
  `..\sharpcap-focus-temperature\sharpcap_focus_state.json`. The previous two-candidate
  fallback list (local directory + sibling repository) has been removed. Use `--state-json`
  only in exceptional cases (e.g. a non-standard clone layout).
- `SHARPCAP_FOCUSER_PATH` is now a single canonical constant pointing exclusively to
  `..\sharpcap-focus-temperature\sharpcap_focuser.py`. No local candidate is searched.

### Removed

- `run_update_reference.bat`: superseded by the automatic `refresh_state_json()` call
  inside `focus_sequencer.py`. For manual/diagnostic refresh, call
  `sharpcap_focuser.py` directly from the command line.

## [1.0.0] - 2026-08-25

### Added

- Initial release.
- `focus_sequencer.py` — on-demand thermal focus compensator for ZWO EAF via ASCOM.
  - Reads regression model and autofocus reference from `sharpcap_focus_state.json`
    produced by [sharpcap-focus-temperature](https://github.com/davidglt/sharpcap-focus-temperature).
  - Connects to the ZWO EAF via ASCOM (`pywin32`) and reads the external temperature sensor.
  - Calculates thermally compensated target position using:
    `focus_target = focus_ref + TCF × (T_current − T_ref)`
  - Moves the focuser to the target position and waits for completion.
  - Updates `last_temp_applied` and `last_focus_applied` in the state JSON after each run.
  - `--state-json` option: path to the JSON state file.
  - `--ascom-id` option: ASCOM ProgID of the focuser driver.
  - `--dry-run` option: calculates and prints target without moving the focuser.
  - `--move-timeout` option: configurable timeout for focuser move (default 60 s).
- `detect_focusers.py` — utility to identify ASCOM ProgIDs of connected ZWO EAF units.
  - Probes `ASCOM.ZWO.Focuser` through `ASCOM.ZWO.Focuser4`.
  - Prints `Name`, `Description`, `Position`, and `Temperature` for each responding focuser.
  - Useful when multiple EAF units are connected (e.g. main tube + guide tube).
- `requirements/requirements.txt` — `pywin32` dependency.
- `.gitignore` — excludes `sharpcap_focus_state.json` (runtime artifact), virtual
  environments, Python cache files, and OS files.
- `README.md` — full documentation including formula, usage, options table,
  multiple EAF identification guide, typical workflow, and state JSON reference.
