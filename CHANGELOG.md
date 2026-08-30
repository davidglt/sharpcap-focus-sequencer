# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.3.1] - 2026-08-30

### Fixed

- **Critical bug — guide tube model silently null:** `refresh_state_json()` in
  `focus_sequencer.py` was not passing `--tube guide` to `sharpcap_focuser.py`
  when refreshing `sharpcap_focus_state_guide.json`.  As a result the producer
  ran with main-tube position defaults (24 000 – 27 000 steps), no guide-tube
  autofocus entries passed the filter, and the guide state JSON was written with
  `model_tcf: null`, causing the sequencer to abort on the next correction cycle
  with *"model_tcf is null"*.  The `tube` argument is now forwarded correctly.

### Added

- `run_focus_guide.bat`: new wrapper for the guide tube (50ED + ASI224MC).
  Passes `--ascom-id ASCOM.EAF_2.Focuser` and `--state-json` pointing to
  `sharpcap_focus_state_guide.json` in the sibling repository.
  Device Hub is not needed because SharpCap does not access the guide tube EAF.

### Changed

- `focus_sequencer.py` docstring: ASCOM ProgID table corrected after CH341T
  USB-serial adapter caused driver re-enumeration:
    - `ASCOM.EAF.Focuser`   → main tube  (C8 + ASI2600MC Pro, ~25 000 steps)
    - `ASCOM.EAF_2.Focuser` → guide tube (50ED + ASI224MC,     ~335 000 steps)
  New *Guide tube* paragraph explains direct ASCOM access (no Device Hub).
  Wrappers list updated to include `run_focus_guide.bat`.
- README: same ProgID correction applied to the Multiple EAF table.
- README: new *ASCOM access per tube* table documents Device Hub vs. direct access.
- README: two-repository tree updated with guide tube state JSON and
  `run_focus_guide.bat`.
- README: *State JSON location* section replaced with a per-tube table.
- README: *Typical workflow* updated: step 2 added for guide tube nightly run;
  main tube steps renumbered.
- README: *Regenerating the state JSON manually* updated with guide tube example.
- README: *Nightly imaging loop* diagram updated to show guide tube focus at
  session start.

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
  - Probes `ASCOM.EAF.Focuser` through `ASCOM.EAF_5.Focuser`.
  - Prints `Name`, `Description`, `Position`, and `Temperature` for each responding focuser.
  - Useful when multiple EAF units are connected (e.g. main tube + guide tube).
- `requirements/requirements.txt` — `pywin32` dependency.
- `.gitignore` — excludes `sharpcap_focus_state.json` (runtime artifact), virtual
  environments, Python cache files, and OS files.
- `README.md` — full documentation including formula, usage, options table,
  multiple EAF identification guide, typical workflow, and state JSON reference.

[Unreleased]: https://github.com/davidglt/sharpcap-focus-sequencer/compare/v1.3.1...HEAD
[1.3.1]: https://github.com/davidglt/sharpcap-focus-sequencer/compare/v1.0.0...v1.3.1
[1.0.0]: https://github.com/davidglt/sharpcap-focus-sequencer/releases/tag/v1.0.0
