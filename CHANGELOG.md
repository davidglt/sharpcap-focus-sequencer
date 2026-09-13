# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added optional per-tube persistent focus configuration through
  `focus_config.properties`.
- Added `focus_config.properties.example` as a versioned template containing
  independent `[main]` and `[guide]` sections.
- Added `focus_center` and `focus_range` settings for each optical tube.
- Added `--config` to select an alternative `.properties` configuration file.
- Added `--focus-center` for a temporary one-run focus-reference override.
- Added `--focus-range` for a temporary interval-width override, used with
  `--focus-center`.
- Added validation for configuration and CLI focus values:
  `focus_center` must be non-negative; `focus_range` must be a positive even
  integer; and the calculated lower interval limit must be non-negative.

### Changed

- Main C8 + ASI2600MC Pro focus defaults changed from the former
  24,000–27,000-step interval, centred on approximately 25,500 steps, to
  17,200–20,200 steps, centred on 18,700 steps.
- Focus interval limits and fixed X-axis limits are now derived automatically
  from the selected tube's `focus_center` and `focus_range`.
- `--tube main` and `--tube guide` now select their own independent persistent
  focus-reference configuration.
- Command-line `--focus-center` and `--focus-range` values take precedence for
  the current execution but do not rewrite `focus_config.properties`.
- Explicit `--min-position` / `--max-position` overrides have higher priority
  than file configuration; explicit `--x-min` / `--x-max` values remain
  available for chart-only range control.
- Documentation now recommends recalibrating the `[guide]` focus reference
  after changes to the guiding train, including installation of a UV/IR-cut
  filter in front of the ASI224MC.

### Notes

- The external focus configuration affects only the log filtering interval and
  chart axis limits. It does not command, move, home, reset or otherwise alter
  the physical ZWO EAF or its driver coordinate system.
- Local `focus_config.properties` is excluded from version control so each
  observatory can retain its own mechanical and optical references without
  committing them.
- After an optical-train change, verify the new focus centre with several
  autofocus measurements before reducing the interval width or combining the
  new results with historical data.
- For the 50ED guide scope, a UV/IR-cut filter introduces optical glass and may
  shift the best-focus coordinate; measure and save the new `[guide]`
  `focus_center` rather than assuming the old value remains valid.

## [1.4.0] - 2026-08-30

### Added

- Added synthetic focus datasets for both supported optical tubes:
  `sharpcap_synthetic_data_focus.csv` for the C8 main tube and
  `sharpcap_synthetic_data_focus_guide.csv` for the 50ED guide tube.
- Synthetic datasets are loaded automatically when present beside their
  corresponding output CSV.
- Added a `Synthetic` yes/no column to the removed-outliers CSV so expelled
  synthetic samples are clearly identified.
- Added a dedicated README section, "Synthetic data (Bayesian prior)",
  including the guide-tube example table and operational behaviour.

### Changed

- Renamed generated focus-data CSVs from `sharpcap_final_focus*` to
  `sharpcap_data_focus*` for both tubes.
- Updated synthetic datasets to cover representative Madrid night
  temperatures, including summer values up to 34 °C (July) and 30 °C
  (August).
- Tuned synthetic reference models to -30 steps/°C (C8) and
  -1000 steps/°C (50ED guide tube).
- Synthetic focus samples now participate in studentized-residual outlier
  filtering under the same rules as real autofocus samples.
- Updated chart focus-table ordering so `Autofocus pts` follows
  `Delta focus`.
- Updated chart legend and focus table to display real autofocus point counts
  correctly.
- Runtime-generated data, chart, state and outlier files remain excluded from
  version control through `.gitignore`.

### Fixed

- Corrected the 50ED guide-tube operating range to 315,000–365,000 focuser
  steps throughout code (`TUBE_DEFAULTS`), module docstring, `--tube` help
  text, README tube table and README note.
- Corrected synthetic-point counting to use surviving samples after outlier
  filtering.
- Added validation for invalid minimum/maximum position range arguments.
- Added focus-position range check to catch out-of-range EAF positions.
- Improved dry-run documentation: clarified that dry-run skips the state-JSON
  refresh.
- Added temperature-coefficient sign note and slope/inverse-coefficient
  consistency guidance to the docstring.
- Fixed minor PEP 8 indentation issues.

## [1.3.2] - 2026-08-30

### Fixed

- Guide tube position range corrected: 330,000–370,000 → **325,000–365,000**
  steps.
- Typical operating position updated from approximately 335,000 to
  **345,000** steps.
- Updated in `TUBE_DEFAULTS["guide"]` (`min_position`, `max_position`,
  `x_min`, `x_max`), module docstring, `--tube` help text, README tube table,
  README note and README command-line options table.

## [1.3.1] - 2026-08-30

### Fixed

- **Two-repo bug (`sharpcap-focus-sequencer`):** `refresh_state_json()` in the
  sibling sequencer repository was not passing `--tube guide` to
  `sharpcap_focuser.py` when refreshing `sharpcap_focus_state_guide.json`.
  As a result, the producer ran with main-tube position defaults
  (24,000–27,000 steps), no guide-tube autofocus entries passed the filter,
  and the guide state JSON was written with `model_tcf: null`, causing the
  sequencer to abort on the next cycle. Fixed in
  [sharpcap-focus-sequencer commit e88076f](https://github.com/davidglt/sharpcap-focus-sequencer/commit/e88076f33535a31e80e867424024325232cf3b1f).

### Documentation

- README: expanded guide-tube row in the tube table to show the full position
  filter range alongside the typical operating position, so users can adjust
  `--min-position` / `--max-position` without reading the source code.
- README: added note on the 50ED EAF Max Steps setting (520,000 in ASICap) and
  when to use `--min-position` / `--max-position` overrides.
- README: `--tube` option description in the command-line table now includes
  the position range for each tube.

## [1.3.0] - 2026-08-25

### Added

- `--tube {main,guide}` argument: selects per-tube defaults for position range,
  output file names and chart title.
  - `main` (default): C8 + ASI2600MC Pro, 24,000–27,000 steps,
    `sharpcap_focus_state.json`.
  - `guide`: 50ED + ASI224MC, 325,000–365,000 steps,
    `sharpcap_focus_state_guide.json`.
- Synthetic-data support: if a
  `sharpcap_synthetic_data_focus[_guide].csv` file exists beside the output
  CSV, it is merged with real data before regression. Synthetic points are
  immune to outlier removal, plotted in green and excluded from the state JSON
  reference.
- `sharpcap_synthetic_data_focus.csv` and
  `sharpcap_synthetic_data_focus_guide.csv` example files added to the
  repository.

### Changed

- Chart title now includes the tube label, for example
  *Focuser Position vs Temperature — Guide tube 50ED*.
- Legend entries now show point counts for autofocus results and removed
  outliers.
- Focus table in the chart now lists First focus / Last focus / Delta focus
  before the point-count rows.

## [1.2.0] - 2026-08-20

### Added

- `--last-days N` argument: restricts the dataset to the last N calendar days
  (today counts as day 1).
- Outlier removal using externally studentized residuals (`statsmodels`).
  - Removed points are written to a separate `*_removed_outliers.csv` file.
  - `--no-remove-outliers` disables the filter.
  - `--studentized-threshold` sets the rejection cutoff (default 3.0).
- State JSON export (`sharpcap_focus_state.json`) with last valid autofocus
  reference (`timestamp_ref`, `temp_ref`, `focus_ref`) and regression model
  (`model_tcf`, `model_inv_tcf`, `model_intercept_c`).
- `last_temp_applied` and `last_focus_applied` fields in the state JSON so an
  external sequencer can track the current compensation state at runtime.

### Changed

- Legend entries now show count of autofocus results and removed outliers.
- Regression line extended to axis limits with a dashed style outside the
  measured data range.

## [1.1.0] - 2026-08-10

### Added

- `--predict-temperature` argument: draws a prediction marker and annotation on
  the chart and prints the predicted focuser position to the console.
- Summary tables (Model and Focus) added to the right panel of the chart.
- `--auto-axis` flag: uses automatic axis scaling instead of fixed limits.
- `--x-min`, `--x-max`, `--y-min` and `--y-max` arguments for manual axis
  control.

### Changed

- Chart layout: main scatter area left-aligned with a wider right panel for
  legend and tables.

## [1.0.0] - 2026-08-01

### Added

- Initial release.
- Parses SharpCap `Log_*.log` files and extracts autofocus results.
- Filters results by focuser step range (`--min-position`, `--max-position`).
- Fits a linear regression between focuser position and temperature.
- Exports cleaned results to CSV.
- Generates a chart with regression line, scatter plot and legend.
- `--log-path` argument for custom SharpCap log folder.
- `--output-csv` argument for custom output CSV path.

[Unreleased]: https://github.com/davidglt/sharpcap-focus-temperature/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/davidglt/sharpcap-focus-temperature/compare/v1.3.2...v1.4.0
[1.3.2]: https://github.com/davidglt/sharpcap-focus-temperature/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/davidglt/sharpcap-focus-temperature/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/davidglt/sharpcap-focus-temperature/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/davidglt/sharpcap-focus-temperature/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/davidglt/sharpcap-focus-temperature/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/davidglt/sharpcap-focus-temperature/releases/tag/v1.0.0
