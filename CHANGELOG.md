# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `--min-correction` option: minimum correction in steps required to trigger a move
  when a backlash overshoot would be needed (target < current position). Default: 50 steps
  (~0.81 °C with TCF = −61.59 steps/°C). Set to `0` to always move in both directions.

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
  the supplied value is used instead. The move and state JSON update are still skipped.

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
