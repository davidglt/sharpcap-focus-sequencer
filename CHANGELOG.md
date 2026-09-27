# Changelog

All notable changes to `sharpcap-focus-sequencer` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added a configurable timeout for thermal-model refresh subprocesses.
- Added per-ASCOM-driver execution locks and a final busy check before each
  focuser movement.
- Added atomic replacement for sequencer runtime updates to the focus-state
  JSON.
- Added finite-number and focus-position validation for loaded state JSON.
- Added `focus_sequencer.properties.example` as the versioned template for
  local main-tube sequencer configuration.
- Added configurable filter focus offsets for main-tube filter positions 1
  through 7.
- Added `--config PATH` to select an alternative main-tube sequencer
  properties file.
- Added `--filter POSITION` and `-f POSITION` to select the main-tube target
  filter position.
- Added main-tube filter position 1, `No filter`, as the default thermal-model
  reference with an offset of `0` EAF steps.
- Added main-tube filter position 2, `Optolong L-eNhance Nebular`, with an
  initial configurable offset of `+500` EAF steps.
- Added filter, offset, base thermal target and final target information to
  sequencer log lines.
- Added ASCOM target-limit checking after the selected filter offset is added.

### Changed

- Main-tube target calculation now applies the selected filter offset after
  the no-filter thermal prediction:

  ```text
  base_target  = focus_ref + TCF × (T_current - T_ref)
  final_target = base_target + filter_offset_steps
  ```

- The thermal model remains referenced to autofocus measurements made with
  main-tube filter position 1, `No filter`.
- `last_focus_applied` records the final physical EAF target, including the
  main-tube filter offset.
- Filter offsets apply only to the C8 main imaging train. The 50ED guide tube
  always uses a zero filter offset and rejects `--filter` and `--config`.
- Rebuilt this changelog as a sequencer-specific history after removing
  unrelated content from `sharpcap-focus-temperature`.

## [1.4.0] - 2026-08-30

### Added

- Added support for two independently operated optical tubes:
  - Main tube: Celestron C8 + ASI2600MC Pro through ASCOM Device Hub.
  - Guide tube: Sky-Watcher 50ED + ASI224MC through direct ZWO EAF ASCOM
    access.
- Added `run_focus_guide.bat` for guide-tube execution with
  `ASCOM.EAF_2.Focuser` and `sharpcap_focus_state_guide.json`.
- Added `detect_focusers.py` to identify available ZWO EAF ASCOM ProgIDs,
  positions and sensor temperatures.
- Added automatic state JSON refresh by invoking the sibling
  `sharpcap_focuser.py` before each non-dry-run correction.
- Added automatic producer-interpreter discovery, using the sibling
  `sharpcap-focus-temperature` virtual environment when available.
- Added tube detection from the state JSON filename, so producer refreshes use
  `--tube guide` for guide state files and `--tube main` otherwise.

### Changed

- Standardized the two-repository layout: state JSON files and their producer
  remain in `sharpcap-focus-temperature`; the sequencer consumes them from the
  sibling repository rather than storing copies.
- Updated wrapper scripts to resolve paths relative to their own location,
  making installations portable across parent directories.
- Updated documentation for Device Hub use on the main EAF and direct ASCOM
  access on the guide EAF.
- Updated documentation after ZWO EAF ProgID reassignment caused by changes to
  USB topology.

### Fixed

- Fixed guide-tube state refresh by passing `--tube guide` when regenerating
  `sharpcap_focus_state_guide.json`.
- Fixed the producer invocation to use the sibling project virtual environment
  instead of the sequencer virtual environment, which does not necessarily
  contain `numpy`, `statsmodels` and `matplotlib`.
- Fixed the state JSON refresh path so the producer writes to the exact JSON
  file subsequently read by the sequencer.

## [1.3.1] - 2026-08-30

### Fixed

- Corrected the critical guide-tube refresh issue where the state producer was
  invoked without `--tube guide`, causing guide autofocus samples to be
  filtered using main-tube defaults and potentially producing
  `model_tcf: null`.

### Changed

- Updated README guidance for guide-tube EAF position limits and maximum travel
  configuration in ASICap.
- Clarified the relationship between the guide state JSON, guide wrapper and
  second EAF ASCOM ProgID.

## [1.3.0] - 2026-08-25

### Added

- Added the guide-tube operating workflow based on
  `sharpcap_focus_state_guide.json`.
- Added a dedicated guide-tube wrapper script and documentation for direct
  EAF access.
- Added support for selecting state JSON files using `--state-json`.

### Changed

- Expanded the two-repository documentation to distinguish the main and guide
  optical trains.
- Updated the nightly workflow to include an independent guide-tube starting
  focus correction.

## [1.2.0] - 2026-08-25

### Added

- Added automatic state JSON refresh before live corrections.
- Added `--dry-run` mode, which reads focuser position and temperature without
  moving the EAF or refreshing the state JSON.
- Added `--temp` to override the measured temperature, primarily for dry-run
  and diagnostic scenarios.
- Added periodic sequencer logging to dated files under `logs/`.
- Added explicit start, information, skip, error and end log records.

### Changed

- Made the sibling-repository state JSON the canonical source of focus model
  data.
- Updated the main wrapper and documentation for scheduler-friendly,
  on-demand execution.

## [1.1.0] - 2026-08-24

### Added

- Added backlash-compensated EAF movement.
- Added `--backlash` to configure or disable backlash compensation.
- Added `--min-correction` to avoid insignificant corrections only when a
  backlash overshoot would be required.
- Added `--move-timeout` to define the maximum wait time for each focuser
  movement.
- Added busy detection through ASCOM `IsMoving`; the sequencer exits without
  moving the EAF when SharpCap autofocus is already in progress.

### Changed

- The focuser approaches a target from below whenever backlash compensation is
  required, improving movement repeatability.
- Small moves in the favourable direction continue to be applied, while small
  moves requiring a backlash overshoot may be skipped.

## [1.0.0] - 2026-08-24

### Added

- Initial release of SharpCap Focus Sequencer.
- Reads a thermal focus model and autofocus reference from
  `sharpcap_focus_state.json`.
- Reads the current external-sensor temperature from a ZWO EAF through ASCOM.
- Calculates a thermal target using:

  ```text
  focus_target = focus_ref + TCF × (T_current - T_ref)
  ```

- Moves a ZWO EAF through ASCOM.
- Supports configurable ASCOM ProgID and state JSON path.

[Unreleased]: https://github.com/davidglt/sharpcap-focus-sequencer/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/davidglt/sharpcap-focus-sequencer/compare/v1.3.1...v1.4.0
[1.3.1]: https://github.com/davidglt/sharpcap-focus-sequencer/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/davidglt/sharpcap-focus-sequencer/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/davidglt/sharpcap-focus-sequencer/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/davidglt/sharpcap-focus-sequencer/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/davidglt/sharpcap-focus-sequencer/releases/tag/v1.0.0