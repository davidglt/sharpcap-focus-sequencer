# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
