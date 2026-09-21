#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 David González López-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
SharpCap Focus Sequencer — On-demand thermal focus compensator.

Reads the regression model and last autofocus reference produced by
sharpcap-focus-temperature (sharpcap_focus_state.json), queries the
current temperature from the ZWO EAF external sensor via ASCOM, and
moves the focuser to the thermally compensated position.

The thermal model for the main tube must be calibrated with filter position 1,
"No filter". A selected main-tube capture filter contributes its configured
focus offset to the no-filter thermal target:

    base_focus_target = focus_ref + TCF * (T_current - T_ref)
    final_focus_target = base_focus_target + filter_offset_steps

Filter offsets apply only to the main optical tube:

    Main tube:  Celestron C8 + F/6.3 reducer + ASI2600MC Pro + ZWO EAF
    Guide tube: Sky-Watcher 50ED + ASI224MC + second ZWO EAF

The guide tube uses its own thermal model and always applies a zero filter
offset. Supplying --filter for a guide-tube state JSON is an error.

Main-tube filter definitions are loaded from focus_sequencer.properties.
Copy focus_sequencer.properties.example to focus_sequencer.properties and
adjust the filter names and offsets for the local observatory.

Backlash compensation
---------------------
The focuser always arrives at the target from below. If the target is below
the current position, the script moves to (target - backlash_steps) and then
moves outward to the target. Set --backlash 0 to disable compensation.

Minimum correction threshold
-----------------------------
The --min-correction threshold applies only when a backlash overshoot would
be needed. When no backlash is needed, the script always moves to keep focus
continuously corrected without accumulating drift.

Busy detection
--------------
If the focuser is already moving after connection, for example because
SharpCap is running autofocus, the script exits without moving the focuser.
A scheduled sequencer can retry later.

State JSON
----------
Both the state JSON and sharpcap_focuser.py belong to the sibling repository
sharpcap-focus-temperature. Before each non-dry run, this script refreshes the
selected state JSON from current SharpCap logs after the focuser busy check.

Usage
-----
    python focus_sequencer.py
    python focus_sequencer.py --filter 2
    python focus_sequencer.py --filter 2 --dry-run
    python focus_sequencer.py --config custom.properties --filter 2
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


DEG_C = "°C"
DELTA = "d"

STATE_JSON_FILENAME = "sharpcap_focus_state.json"
DEFAULT_ASCOM_ID = "ASCOM.DeviceHub.Focuser"
DEFAULT_BACKLASH_STEPS = 500
DEFAULT_MIN_CORRECTION = 50
MOVE_TIMEOUT_S = 60
MOVE_POLL_INTERVAL_S = 0.5

DEFAULT_CONFIG_FILENAME = "focus_sequencer.properties"
EXAMPLE_CONFIG_FILENAME = "focus_sequencer.properties.example"
MIN_FILTER_POSITION = 1
MAX_FILTER_POSITION = 7

SHARPCAP_FOCUSER_PATH = (
    Path(__file__).resolve().parent.parent
    / "sharpcap-focus-temperature"
    / "sharpcap_focuser.py"
)

STATE_JSON_PATH = (
    Path(__file__).resolve().parent.parent
    / "sharpcap-focus-temperature"
    / STATE_JSON_FILENAME
)

START_LEVEL = 25
logging.addLevelName(START_LEVEL, "START")
logging.addLevelName(logging.INFO, "INFO ")
logging.addLevelName(logging.WARNING, "SKIP ")
logging.addLevelName(logging.ERROR, "ERROR")


@dataclass(frozen=True)
class FilterDefinition:
    """A main-tube filter-wheel position and its focus offset."""

    position: int
    name: str
    offset_steps: int


@dataclass(frozen=True)
class SequencerConfig:
    """Main-tube filter configuration loaded from a properties file."""

    source_path: Path
    default_filter: int
    filters: dict[int, FilterDefinition]

    def get_filter(self, position: int) -> FilterDefinition:
        try:
            return self.filters[position]
        except KeyError as exc:
            raise ValueError(
                f"Filter position {position} is not configured in {self.source_path}."
            ) from exc


@dataclass(frozen=True)
class ActiveFilter:
    """Filter context used by one sequencer execution."""

    position: int | None
    name: str
    offset_steps: int
    applies_to_main_tube: bool

    @property
    def label(self) -> str:
        if self.position is None:
            return self.name
        return f"{self.position} ({self.name})"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    """Create the daily file logger and a stdout handler."""
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)

    log_path = log_dir / f"{datetime.now().strftime('%Y%m%d')}_focus_sequencer.log"
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger = logging.getLogger("focus_sequencer")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def log_start(log: logging.Logger, message: str) -> None:
    """Emit a START-level log message."""
    log.log(START_LEVEL, message)


# ---------------------------------------------------------------------------
# Properties configuration
# ---------------------------------------------------------------------------

def parse_properties_file(path: Path) -> dict[str, str]:
    """Read a simple key=value or key:value properties file."""
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {path}\n"
            f"Copy {EXAMPLE_CONFIG_FILENAME} to {DEFAULT_CONFIG_FILENAME} "
            "and configure the main-tube filter offsets."
        )

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OSError(f"Could not read configuration file {path}: {exc}") from exc

    properties: dict[str, str] = {}

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()

        if not line or line.startswith("#") or line.startswith(";"):
            continue

        separator_index = -1
        for separator in ("=", ":"):
            separator_index = line.find(separator)
            if separator_index >= 0:
                break

        if separator_index < 1:
            raise ValueError(
                f"Invalid configuration line {line_number} in {path}: "
                f"expected key=value, got {raw_line!r}"
            )

        key = line[:separator_index].strip()
        value = line[separator_index + 1:].strip()

        if not key:
            raise ValueError(
                f"Invalid empty key on configuration line {line_number} in {path}."
            )

        properties[key] = value

    return properties


def require_property(properties: dict[str, str], key: str, path: Path) -> str:
    """Return a required property value."""
    value = properties.get(key)

    if value is None or not value.strip():
        raise ValueError(f"Missing required property {key!r} in {path}.")

    return value.strip()


def parse_int_property(
    properties: dict[str, str],
    key: str,
    path: Path,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Read and validate an integer property."""
    raw_value = require_property(properties, key, path)

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"Property {key!r} in {path} must be an integer, got {raw_value!r}."
        ) from exc

    if minimum is not None and value < minimum:
        raise ValueError(
            f"Property {key!r} in {path} must be >= {minimum}, got {value}."
        )

    if maximum is not None and value > maximum:
        raise ValueError(
            f"Property {key!r} in {path} must be <= {maximum}, got {value}."
        )

    return value


def load_sequencer_config(config_path: Path) -> SequencerConfig:
    """Load and validate main-tube filter definitions."""
    source_path = config_path.expanduser().resolve()
    properties = parse_properties_file(source_path)

    default_filter = parse_int_property(
        properties,
        "filter.default",
        source_path,
        minimum=MIN_FILTER_POSITION,
        maximum=MAX_FILTER_POSITION,
    )

    filters: dict[int, FilterDefinition] = {}

    for position in range(MIN_FILTER_POSITION, MAX_FILTER_POSITION + 1):
        name = require_property(properties, f"filter.{position}.name", source_path)
        offset_steps = parse_int_property(
            properties,
            f"filter.{position}.offset_steps",
            source_path,
        )

        filters[position] = FilterDefinition(
            position=position,
            name=name,
            offset_steps=offset_steps,
        )

    if filters[1].offset_steps != 0:
        raise ValueError(
            f"filter.1.offset_steps must be 0 in {source_path}; "
            "filter 1 is the no-filter thermal-model reference."
        )

    return SequencerConfig(
        source_path=source_path,
        default_filter=default_filter,
        filters=filters,
    )


def resolve_config_path(cli_path: str | None) -> Path:
    """Return the local main-tube configuration file path."""
    if cli_path:
        return Path(cli_path).expanduser().resolve()

    return Path(__file__).resolve().parent / DEFAULT_CONFIG_FILENAME


# ---------------------------------------------------------------------------
# State and tube helpers
# ---------------------------------------------------------------------------

def resolve_state_json(cli_path: str | None) -> Path:
    """Return the selected state JSON path."""
    if cli_path is not None:
        state_path = Path(cli_path).expanduser().resolve()

        if not state_path.exists():
            raise FileNotFoundError(f"--state-json path not found: {state_path}")

        return state_path

    if not STATE_JSON_PATH.exists():
        raise FileNotFoundError(
            f"sharpcap_focus_state.json not found at: {STATE_JSON_PATH}\n"
            "Run sharpcap_focuser.py in sharpcap-focus-temperature first."
        )

    return STATE_JSON_PATH


def detect_tube(state_json_path: Path) -> str:
    """Identify the optical tube from the selected state JSON filename."""
    return "guide" if "guide" in state_json_path.name.lower() else "main"


def load_state(state_json_path: Path) -> dict:
    """Read and validate the thermal-model state JSON."""
    try:
        with state_json_path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in state file {state_json_path}: {exc}") from exc

    required = [
        "focus_ref",
        "temp_ref",
        "model_tcf",
        "last_temp_applied",
        "last_focus_applied",
    ]

    missing = [key for key in required if key not in state]
    if missing:
        raise ValueError(f"State JSON is missing required fields: {missing}")

    if state["model_tcf"] is None:
        raise ValueError("model_tcf is null in the state JSON.")

    return state


def resolve_producer_python(log: logging.Logger) -> str:
    """Return the sibling producer repository Python interpreter."""
    sibling_root = SHARPCAP_FOCUSER_PATH.parent
    candidates = [
        sibling_root / ".venv" / "Scripts" / "python.exe",
        sibling_root / ".venv" / "bin" / "python",
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    log.warning(
        f"UPDATE — sibling .venv not found at {sibling_root / '.venv'}; "
        "falling back to sys.executable"
    )
    return sys.executable


def refresh_state_json(state_json_path: Path, tube: str, log: logging.Logger) -> dict | None:
    """Refresh the selected state JSON from current SharpCap logs."""
    if not SHARPCAP_FOCUSER_PATH.exists():
        log.error(
            f"UPDATE FAILED — sharpcap_focuser.py not found at: "
            f"{SHARPCAP_FOCUSER_PATH}"
        )
        return None

    producer_python = resolve_producer_python(log)
    sibling_root = str(SHARPCAP_FOCUSER_PATH.parent)

    try:
        result = subprocess.run(
            [
                producer_python,
                str(SHARPCAP_FOCUSER_PATH),
                "--tube",
                tube,
                "--output-state-json",
                str(state_json_path),
            ],
            capture_output=True,
            text=True,
            cwd=sibling_root,
            check=False,
        )
    except OSError as exc:
        log.error(f"UPDATE FAILED — could not start sharpcap_focuser.py: {exc}")
        return None

    if result.returncode != 0:
        stderr = result.stderr.strip().replace("\n", " ") or "(no stderr)"
        log.error(f"UPDATE FAILED (rc={result.returncode}): {stderr}")
        return None

    try:
        fresh_state = load_state(state_json_path)
    except (FileNotFoundError, ValueError) as exc:
        log.error(f"UPDATE FAILED — could not reload state JSON after refresh: {exc}")
        return None

    timestamp_ref = fresh_state.get("timestamp_ref", "unknown")
    focus_ref = fresh_state.get("focus_ref", "?")
    temp_ref = fresh_state.get("temp_ref", "?")
    tcf = fresh_state.get("model_tcf", "?")

    temp_text = f"{temp_ref:.2f}{DEG_C}" if isinstance(temp_ref, float) else str(temp_ref)
    tcf_text = f"{tcf:.2f}" if isinstance(tcf, float) else str(tcf)

    log.info(
        f"UPDATE OK — tube={tube} | ref={timestamp_ref} | "
        f"focus_ref={focus_ref} | T_ref={temp_text} | TCF={tcf_text}"
    )

    return fresh_state


def save_state(state: dict, state_json_path: Path) -> None:
    """Persist the runtime fields in the shared state JSON."""
    with state_json_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


# ---------------------------------------------------------------------------
# ASCOM helpers
# ---------------------------------------------------------------------------

def connect_focuser(ascom_id: str):
    """Connect to an ASCOM focuser and return the COM driver object."""
    try:
        import win32com.client
    except ImportError as exc:
        raise ImportError(
            "pywin32 is required. Install it with: pip install pywin32"
        ) from exc

    focuser = win32com.client.Dispatch(ascom_id)
    focuser.Connected = True

    if not focuser.Connected:
        raise RuntimeError(f"Could not connect to ASCOM focuser: {ascom_id}")

    return focuser


def disconnect_focuser(focuser, log: logging.Logger) -> None:
    """Disconnect without masking the preceding operation result."""
    if focuser is None:
        return

    try:
        focuser.Connected = False
    except Exception as exc:
        log.warning(f"Could not disconnect ASCOM focuser cleanly: {exc}")


def check_not_busy(focuser) -> bool:
    """Return whether the focuser can accept a new movement."""
    try:
        return not bool(focuser.IsMoving)
    except Exception as exc:
        raise RuntimeError(f"Could not read IsMoving from focuser: {exc}") from exc


def read_temperature(focuser) -> float:
    """Read the external EAF temperature sensor."""
    try:
        temperature = focuser.Temperature
    except Exception as exc:
        raise RuntimeError(f"Could not read temperature: {exc}") from exc

    if temperature is None:
        raise RuntimeError(
            "Focuser returned None for Temperature — check the EAF sensor."
        )

    return float(temperature)


def read_position(focuser) -> int:
    """Read the current commanded focuser position."""
    try:
        return int(focuser.Position)
    except Exception as exc:
        raise RuntimeError(f"Could not read focuser position: {exc}") from exc


def get_focuser_limits(focuser) -> tuple[int, int | None]:
    """Return the lower and available upper ASCOM focuser limits."""
    minimum = 0
    maximum: int | None = None

    try:
        max_step = int(focuser.MaxStep)
        if max_step > 0:
            maximum = max_step
    except Exception:
        maximum = None

    return minimum, maximum


def clamp_target_to_limits(
    target: int,
    minimum: int,
    maximum: int | None,
) -> tuple[int, bool]:
    """Clamp a requested target to known ASCOM focuser limits."""
    clamped_target = max(target, minimum)

    if maximum is not None:
        clamped_target = min(clamped_target, maximum)

    return clamped_target, clamped_target != target


def move_focuser(focuser, target: int, timeout_s: float) -> int:
    """Move the focuser and wait for the ASCOM driver to finish."""
    focuser.Move(target)
    deadline = time.monotonic() + timeout_s

    while focuser.IsMoving:
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Focuser did not reach position {target} within {timeout_s:.0f} s."
            )
        time.sleep(MOVE_POLL_INTERVAL_S)

    return int(focuser.Position)


def move_focuser_with_backlash(
    focuser,
    target: int,
    current_position: int,
    backlash_steps: int,
    timeout_s: float,
) -> int:
    """Move to target while approaching from below when backlash is enabled."""
    if backlash_steps > 0 and target < current_position:
        overshoot = max(target - backlash_steps, 0)
        move_focuser(focuser, overshoot, timeout_s)

    return move_focuser(focuser, target, timeout_s)


# ---------------------------------------------------------------------------
# Command-line arguments
# ---------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    """Parse and validate command-line options."""
    parser = argparse.ArgumentParser(
        description=(
            "On-demand thermal focus compensator for ZWO EAF via ASCOM. "
            "Main-tube filter offsets are loaded from a properties file."
        )
    )

    parser.add_argument(
        "--state-json",
        default=None,
        help="Path to the focus state JSON produced by sharpcap_focuser.py.",
    )

    parser.add_argument(
        "--ascom-id",
        default=DEFAULT_ASCOM_ID,
        help=f"ASCOM focuser ProgID (default: {DEFAULT_ASCOM_ID}).",
    )

    parser.add_argument(
        "--config",
        default=None,
        help=(
            f"Main-tube filter properties path. Defaults to "
            f"{DEFAULT_CONFIG_FILENAME} beside this script."
        ),
    )

    parser.add_argument(
        "--filter",
        "-f",
        dest="filter_position",
        type=int,
        choices=range(MIN_FILTER_POSITION, MAX_FILTER_POSITION + 1),
        default=None,
        metavar="POSITION",
        help=(
            "Main-tube target filter position, from 1 to 7. If omitted, "
            "filter.default from the properties file is used."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Read state, focuser position and temperature without refreshing "
            "the state JSON or moving the focuser."
        ),
    )

    parser.add_argument(
        "--temp",
        type=float,
        default=None,
        metavar="DEGREES",
        help="Override the EAF temperature reading.",
    )

    parser.add_argument(
        "--backlash",
        type=int,
        default=DEFAULT_BACKLASH_STEPS,
        metavar="STEPS",
        help=f"Backlash compensation in steps (default: {DEFAULT_BACKLASH_STEPS}).",
    )

    parser.add_argument(
        "--min-correction",
        type=int,
        default=DEFAULT_MIN_CORRECTION,
        metavar="STEPS",
        help=(
            "Minimum correction only when backlash overshoot is required "
            f"(default: {DEFAULT_MIN_CORRECTION})."
        ),
    )

    parser.add_argument(
        "--move-timeout",
        type=float,
        default=MOVE_TIMEOUT_S,
        metavar="SECONDS",
        help=f"Maximum seconds for each movement (default: {MOVE_TIMEOUT_S}).",
    )

    args = parser.parse_args()

    if args.backlash < 0:
        parser.error("--backlash must be zero or a positive integer.")

    if args.min_correction < 0:
        parser.error("--min-correction must be zero or a positive integer.")

    if args.move_timeout <= 0:
        parser.error("--move-timeout must be greater than zero.")

    return args


# ---------------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------------

def select_active_filter(
    args: argparse.Namespace,
    state_json_path: Path,
) -> tuple[str, ActiveFilter, SequencerConfig | None]:
    """Identify the tube and select its active filter context."""
    tube = detect_tube(state_json_path)

    if tube == "guide":
        if args.filter_position is not None:
            raise ValueError(
                "--filter is supported only for the main tube. "
                "The guide tube always uses a zero filter offset."
            )

        if args.config is not None:
            raise ValueError(
                "--config is supported only for the main tube. "
                "The guide tube does not load filter configuration."
            )

        return (
            tube,
            ActiveFilter(
                position=None,
                name="N/A",
                offset_steps=0,
                applies_to_main_tube=False,
            ),
            None,
        )

    config_path = resolve_config_path(args.config)
    sequencer_config = load_sequencer_config(config_path)

    filter_position = (
        args.filter_position
        if args.filter_position is not None
        else sequencer_config.default_filter
    )

    selected_filter = sequencer_config.get_filter(filter_position)

    return (
        tube,
        ActiveFilter(
            position=selected_filter.position,
            name=selected_filter.name,
            offset_steps=selected_filter.offset_steps,
            applies_to_main_tube=True,
        ),
        sequencer_config,
    )


def main() -> int:
    """Run a single thermal-focus correction cycle."""
    log = setup_logging()
    args = parse_arguments()
    focuser = None

    try:
        state_json_path = resolve_state_json(args.state_json)
        tube, active_filter, sequencer_config = select_active_filter(
            args,
            state_json_path,
        )
        state = load_state(state_json_path)
    except (FileNotFoundError, OSError, ValueError) as exc:
        log.error(f"Configuration error: {exc}")
        log.info("END   | pos=N/A | reason=configuration_error")
        return 1

    focus_ref = int(state["focus_ref"])
    temp_ref = float(state["temp_ref"])
    tcf = float(state["model_tcf"])
    timestamp_ref = state.get("timestamp_ref", "unknown")
    last_focus = state.get("last_focus_applied", "unknown")
    last_temp = state.get("last_temp_applied", "unknown")

    last_temp_text = (
        f"{last_temp:.2f}{DEG_C}"
        if isinstance(last_temp, (int, float))
        else str(last_temp)
    )

    config_text = (
        f" | config={sequencer_config.source_path.name}"
        if sequencer_config is not None
        else ""
    )

    log_start(
        log,
        f"tube={tube} | ref={timestamp_ref} | focus_ref={focus_ref} | "
        f"T_ref={temp_ref:.2f}{DEG_C} | TCF={tcf:.2f} | "
        f"last_focus={last_focus} | last_T={last_temp_text} | "
        f"filter={active_filter.label} | "
        f"filter_offset={active_filter.offset_steps:+d}"
        f"{config_text} | "
        f"backlash={args.backlash} | min_correction={args.min_correction}"
        + (" | DRY_RUN" if args.dry_run else "")
    )

    try:
        focuser = connect_focuser(args.ascom_id)
    except Exception as exc:
        log.error(f"ASCOM connection failed: {exc}")
        log.info("END   | pos=N/A | reason=error")
        return 1

    try:
        if not check_not_busy(focuser):
            current_position = read_position(focuser)
            log.warning(
                "Focuser busy (IsMoving=True) — skipping this cycle, retry later"
            )
            log.info(f"END   | pos={current_position} | reason=busy")
            return 0

        if not args.dry_run:
            fresh_state = refresh_state_json(state_json_path, tube, log)

            if fresh_state is not None:
                state = fresh_state
                focus_ref = int(state["focus_ref"])
                temp_ref = float(state["temp_ref"])
                tcf = float(state["model_tcf"])

        current_position = read_position(focuser)

        current_temperature = (
            args.temp if args.temp is not None else read_temperature(focuser)
        )

        delta_temperature = current_temperature - temp_ref
        base_focus_target = round(focus_ref + tcf * delta_temperature)
        requested_focus_target = base_focus_target + active_filter.offset_steps

        minimum_position, maximum_position = get_focuser_limits(focuser)
        focus_target, target_was_clamped = clamp_target_to_limits(
            requested_focus_target,
            minimum_position,
            maximum_position,
        )

        if target_was_clamped:
            maximum_text = (
                str(maximum_position) if maximum_position is not None else "unknown"
            )
            log.warning(
                f"Requested target={requested_focus_target} is outside ASCOM "
                f"limits [{minimum_position}, {maximum_text}]; "
                f"using clamped target={focus_target}"
            )

        correction = focus_target - current_position
        needs_backlash = args.backlash > 0 and focus_target < current_position

        calculation_text = (
            f"tube={tube} | "
            f"T={current_temperature:.2f}{DEG_C} | "
            f"{DELTA}T={delta_temperature:+.2f}{DEG_C} | "
            f"TCF={tcf:.2f} | "
            f"filter={active_filter.label} | "
            f"filter_offset={active_filter.offset_steps:+d} | "
            f"base_target={base_focus_target} | "
            f"target={focus_target} | "
            f"pos={current_position} | "
            f"correction={correction:+d}"
        )

        if correction == 0:
            log.info(
                f"{calculation_text} | backlash=False | "
                f"final={current_position} | no move needed"
            )
            log.info(f"END   | pos={current_position} | reason=ok")
            return 0

        if needs_backlash and abs(correction) < args.min_correction:
            log.warning(
                f"{calculation_text} | below min_correction="
                f"{args.min_correction} (backlash direction) — skipped"
            )
            log.info(f"END   | pos={current_position} | reason=min_correction")
            return 0

        if args.dry_run:
            log.info(
                f"DRY | {calculation_text} | backlash={needs_backlash} | "
                "move NOT executed"
            )
            log.info(f"END   | pos={current_position} | reason=dry_run")
            return 0

        final_position = move_focuser_with_backlash(
            focuser,
            focus_target,
            current_position,
            args.backlash,
            args.move_timeout,
        )

        if abs(final_position - focus_target) > 5:
            log.warning(
                f"{calculation_text} | backlash={needs_backlash} | "
                f"final={final_position} | WARNING: differs from target "
                f"{focus_target} by more than 5 steps"
            )
            end_reason = "ok_with_warning"
        else:
            log.info(
                f"{calculation_text} | backlash={needs_backlash} | "
                f"final={final_position}"
            )
            end_reason = "ok"

        state["last_temp_applied"] = round(current_temperature, 2)
        state["last_focus_applied"] = focus_target
        save_state(state, state_json_path)

        log.info(f"END   | pos={final_position} | reason={end_reason}")
        return 0

    except (RuntimeError, TimeoutError, OSError, ValueError) as exc:
        current_position_text = "N/A"

        try:
            if focuser is not None:
                current_position_text = str(read_position(focuser))
        except RuntimeError:
            pass

        log.error(f"Focus correction failed: {exc}")
        log.info(f"END   | pos={current_position_text} | reason=error")
        return 1

    finally:
        disconnect_focuser(focuser, log)


if __name__ == "__main__":
    sys.exit(main())