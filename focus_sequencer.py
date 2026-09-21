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

The thermal model must be calibrated with filter position 1, "Sin filtro".
For a selected capture filter, the sequencer adds its configured focus
offset to the reference no-filter position:

    base_focus_target = focus_ref + TCF * (T_current - T_ref)
    focus_target = base_focus_target + filter_offset_steps

Filter definitions and offsets are loaded from focus_sequencer.properties.
Copy focus_sequencer.properties.example to focus_sequencer.properties and
adjust it for the local observatory. The default selected filter is read from
filter.default unless --filter / -f is supplied.

Formula
-------
    base_focus_target = focus_ref + TCF * (T_current - T_ref)
    focus_target      = base_focus_target + filter_offset_steps

Where:
    focus_ref          = focuser position at the reference autofocus point,
                         always measured without a filter
    T_ref              = temperature at the reference autofocus point
    T_current          = current temperature read from the EAF sensor
    TCF                = temperature compensation factor (steps / °C)
    filter_offset_steps = selected filter focus offset in EAF steps

Backlash compensation
---------------------
To eliminate backlash, the focuser always arrives at the target from below
(increasing step numbers = outward direction on C8 + F/6.3). If the target
is below the current position, the script first moves to
(target - backlash_steps) and then moves up to the target.

Minimum correction threshold
-----------------------------
The --min-correction threshold applies only when a backlash overshoot would
be needed (target < current_position). When no backlash is needed
(target >= current_position), the script always moves, even if the correction
is tiny.

Busy detection
--------------
If the focuser is already moving when the script connects (for example while
SharpCap is running autofocus), the script exits cleanly without moving it.

ASCOM access
------------
Main tube:
    ASCOM.DeviceHub.Focuser
    Use Device Hub so SharpCap and this script can access the main EAF.

Guide tube:
    ASCOM.EAF_2.Focuser
    Direct access is suitable when SharpCap does not use the guide EAF.

State JSON
----------
Both the state JSON and the producer script remain exclusively in the sibling
repository sharpcap-focus-temperature. Before each non-dry run, the sequencer
refreshes that state JSON from the latest SharpCap logs after checking that the
focuser is not busy.

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

DEFAULT_ASCOM_ID = "ASCOM.DeviceHub.Focuser"
DEFAULT_BACKLASH_STEPS = 500
DEFAULT_MIN_CORRECTION = 50
MOVE_TIMEOUT_S = 60
MOVE_POLL_INTERVAL_S = 0.5

DEFAULT_CONFIG_FILENAME = "focus_sequencer.properties"
EXAMPLE_CONFIG_FILENAME = "focus_sequencer.properties.example"
MIN_FILTER_POSITION = 1
MAX_FILTER_POSITION = 7

START_LEVEL = 25
logging.addLevelName(START_LEVEL, "START")


@dataclass(frozen=True)
class FilterDefinition:
    """A configured filter-wheel position and its focus offset."""

    position: int
    name: str
    offset_steps: int


@dataclass(frozen=True)
class SequencerConfig:
    """Configuration loaded from the local properties file."""

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


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    """Configure and return the module logger.

    Appends to logs/YYYYMMDD_focus_sequencer.log and also writes to stdout.
    """
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)

    log_filename = log_dir / f"{datetime.now().strftime('%Y%m%d')}_focus_sequencer.log"

    fmt = "%(asctime)s | %(levelname)-5s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logger = logging.getLogger("focus_sequencer")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    file_handler = logging.FileHandler(log_filename, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logger.addHandler(console_handler)

    return logger


logging.addLevelName(logging.INFO, "INFO ")
logging.addLevelName(logging.WARNING, "SKIP ")
logging.addLevelName(logging.ERROR, "ERROR")


def log_start(log: logging.Logger, message: str) -> None:
    """Emit a START-level log line."""
    log.log(START_LEVEL, message)


# ---------------------------------------------------------------------------
# Properties configuration
# ---------------------------------------------------------------------------

def parse_properties_file(path: Path) -> dict[str, str]:
    """Load a simple Java-style key=value properties file.

    Blank lines and lines beginning with # or ; are ignored. Both '=' and ':'
    can separate a key from its value. Values are stored as plain strings.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {path}\n"
            f"Copy {EXAMPLE_CONFIG_FILENAME} to {DEFAULT_CONFIG_FILENAME} "
            "and adjust the filter offsets for this observatory."
        )

    properties: dict[str, str] = {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise OSError(f"Could not read configuration file {path}: {exc}") from exc

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
    """Return a required property or raise a configuration error."""
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
    """Load and validate filter definitions from a properties file."""
    resolved_path = config_path.expanduser().resolve()
    properties = parse_properties_file(resolved_path)

    default_filter = parse_int_property(
        properties,
        "filter.default",
        resolved_path,
        minimum=MIN_FILTER_POSITION,
        maximum=MAX_FILTER_POSITION,
    )

    filters: dict[int, FilterDefinition] = {}

    for position in range(MIN_FILTER_POSITION, MAX_FILTER_POSITION + 1):
        name_key = f"filter.{position}.name"
        offset_key = f"filter.{position}.offset_steps"

        name = require_property(properties, name_key, resolved_path)
        offset_steps = parse_int_property(properties, offset_key, resolved_path)

        filters[position] = FilterDefinition(
            position=position,
            name=name,
            offset_steps=offset_steps,
        )

    if default_filter not in filters:
        raise ValueError(
            f"filter.default={default_filter} is not configured in {resolved_path}."
        )

    reference_filter = filters[1]
    if reference_filter.offset_steps != 0:
        raise ValueError(
            f"filter.1.offset_steps must be 0 in {resolved_path}; "
            "filter 1 is the no-filter reference for the thermal model."
        )

    return SequencerConfig(
        source_path=resolved_path,
        default_filter=default_filter,
        filters=filters,
    )


def resolve_config_path(cli_path: str | None) -> Path:
    """Return the configuration path, defaulting beside this script."""
    if cli_path:
        return Path(cli_path).expanduser().resolve()

    return Path(__file__).resolve().parent / DEFAULT_CONFIG_FILENAME


# ---------------------------------------------------------------------------
# State producer and ASCOM helpers
# ---------------------------------------------------------------------------

def resolve_producer_python(log: logging.Logger) -> str:
    """Return the Python interpreter for sharpcap_focuser.py."""
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
        "falling back to sys.executable (numpy/statsmodels may be missing)"
    )
    return sys.executable


def resolve_state_json(cli_path: str | None) -> Path:
    """Return the state JSON path to use."""
    if cli_path is not None:
        path = Path(cli_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"--state-json path not found: {path}")
        return path

    if not STATE_JSON_PATH.exists():
        raise FileNotFoundError(
            f"sharpcap_focus_state.json not found at: {STATE_JSON_PATH}\n"
            "Run sharpcap_focuser.py in sharpcap-focus-temperature first."
        )

    return STATE_JSON_PATH


def load_state(state_json_path: Path) -> dict:
    """Read and validate the shared focus state JSON."""
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


def refresh_state_json(state_json_path: Path, log: logging.Logger) -> dict | None:
    """Regenerate and reload the state JSON from current SharpCap logs.

    Returns the freshly loaded state on success. Returns None if refreshing
    fails, in which case the caller should continue with the already loaded
    state data.
    """
    if not SHARPCAP_FOCUSER_PATH.exists():
        log.error(
            f"UPDATE FAILED — sharpcap_focuser.py not found at: "
            f"{SHARPCAP_FOCUSER_PATH}"
        )
        return None

    producer_python = resolve_producer_python(log)
    sibling_root = str(SHARPCAP_FOCUSER_PATH.parent)
    tube = "guide" if "guide" in state_json_path.name.lower() else "main"

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

    ref = fresh_state.get("timestamp_ref", "unknown")
    focus_ref = fresh_state.get("focus_ref", "?")
    temp_ref = fresh_state.get("temp_ref", "?")
    tcf = fresh_state.get("model_tcf", "?")

    temp_text = f"{temp_ref:.2f}{DEG_C}" if isinstance(temp_ref, float) else str(temp_ref)
    tcf_text = f"{tcf:.2f}" if isinstance(tcf, float) else str(tcf)

    log.info(
        f"UPDATE OK — ref={ref} | focus_ref={focus_ref} | "
        f"T_ref={temp_text} | TCF={tcf_text}"
    )

    return fresh_state


def connect_focuser(ascom_id: str):
    """Connect to an ASCOM focuser and return its COM object."""
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


def check_not_busy(focuser) -> bool:
    """Return True when the focuser is ready for an external movement."""
    try:
        return not bool(focuser.IsMoving)
    except Exception as exc:
        raise RuntimeError(f"Could not read IsMoving from focuser: {exc}") from exc


def read_temperature(focuser) -> float:
    """Read temperature from the EAF external sensor."""
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
    """Read the commanded EAF position."""
    try:
        return int(focuser.Position)
    except Exception as exc:
        raise RuntimeError(f"Could not read focuser position: {exc}") from exc


def get_focuser_limits(focuser) -> tuple[int, int | None]:
    """Return (minimum, maximum) focuser limits.

    ASCOM focusers conventionally use zero as the minimum. If MaxStep is
    unsupported or invalid, None is returned and only the zero lower bound is
    enforced.
    """
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
    """Clamp a target position to the available mechanical ASCOM limits."""
    clamped = max(target, minimum)

    if maximum is not None:
        clamped = min(clamped, maximum)

    return clamped, clamped != target


def move_focuser(focuser, target: int, timeout_s: float) -> int:
    """Move the focuser and wait until the ASCOM driver reports completion."""
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
    """Move to target while always approaching it from below when needed."""
    if backlash_steps > 0 and target < current_position:
        overshoot = max(target - backlash_steps, 0)
        move_focuser(focuser, overshoot, timeout_s)

    return move_focuser(focuser, target, timeout_s)


def save_state(state: dict, state_json_path: Path) -> None:
    """Persist runtime state after a successful movement."""
    with state_json_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def disconnect_focuser(focuser, log: logging.Logger) -> None:
    """Disconnect from ASCOM without hiding the original operation result."""
    if focuser is None:
        return

    try:
        focuser.Connected = False
    except Exception as exc:
        log.warning(f"Could not disconnect ASCOM focuser cleanly: {exc}")


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    """Parse sequencer command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "On-demand thermal focus compensator for a ZWO EAF via ASCOM. "
            "The thermal model is referenced to filter 1 (Sin filtro); "
            "the selected filter offset is added to the final target."
        )
    )

    parser.add_argument(
        "--state-json",
        default=None,
        help="Path to the state JSON produced by sharpcap_focuser.py.",
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
            f"Path to the filter properties file. Defaults to "
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
            "Target filter-wheel position, from 1 to 7. If omitted, "
            "filter.default from the properties file is used."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate and log the move without moving the focuser or refreshing state.",
    )

    parser.add_argument(
        "--temp",
        type=float,
        default=None,
        metavar="DEGREES",
        help="Override the temperature read from the EAF sensor.",
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
            "Minimum correction required only when a backlash overshoot would "
            f"be needed (default: {DEFAULT_MIN_CORRECTION})."
        ),
    )

    parser.add_argument(
        "--move-timeout",
        type=float,
        default=MOVE_TIMEOUT_S,
        metavar="SECONDS",
        help=f"Timeout for each focuser move (default: {MOVE_TIMEOUT_S}).",
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

def main() -> int:
    log = setup_logging()
    args = parse_arguments()
    focuser = None

    try:
        config_path = resolve_config_path(args.config)
        sequencer_config = load_sequencer_config(config_path)
        selected_filter_position = (
            args.filter_position
            if args.filter_position is not None
            else sequencer_config.default_filter
        )
        selected_filter = sequencer_config.get_filter(selected_filter_position)
    except (FileNotFoundError, OSError, ValueError) as exc:
        log.error(f"Configuration error: {exc}")
        log.info("END   | pos=N/A | reason=configuration_error")
        return 1

    try:
        state_json_path = resolve_state_json(args.state_json)
        state = load_state(state_json_path)
    except (FileNotFoundError, ValueError) as exc:
        log.error(str(exc))
        log.info("END   | pos=N/A | reason=error")
        return 1

    focus_ref = int(state["focus_ref"])
    temp_ref = float(state["temp_ref"])
    tcf = float(state["model_tcf"])
    timestamp_ref = state.get("timestamp_ref", "unknown")
    last_focus = state.get("last_focus_applied", "unknown")
    last_temp = state.get("last_temp_applied", "unknown")

    last_temp_text = (
        f"{last_temp:.2f}{DEG_C}"
        if isinstance(last_temp, (float, int))
        else str(last_temp)
    )

    log_start(
        log,
        f"ref={timestamp_ref} | focus_ref={focus_ref} | "
        f"T_ref={temp_ref:.2f}{DEG_C} | TCF={tcf:.2f} | "
        f"last_focus={last_focus} | last_T={last_temp_text} | "
        f"filter={selected_filter.position} ({selected_filter.name}) | "
        f"filter_offset={selected_filter.offset_steps:+d} | "
        f"config={sequencer_config.source_path.name} | "
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
        ready = check_not_busy(focuser)
    except RuntimeError as exc:
        log.error(f"Focuser busy check failed: {exc}")
        log.info("END   | pos=N/A | reason=error")
        return 1
    finally:
        if focuser is not None and "ready" not in locals():
            disconnect_focuser(focuser, log)

    if not ready:
        try:
            current_position = read_position(focuser)
        except RuntimeError:
            current_position = "N/A"

        log.warning(
            "Focuser busy (IsMoving=True) — skipping this cycle, retry in 7 min"
        )
        log.info(f"END   | pos={current_position} | reason=busy")
        disconnect_focuser(focuser, log)
        return 0

    if not args.dry_run:
        fresh_state = refresh_state_json(state_json_path, log)

        if fresh_state is not None:
            state = fresh_state
            focus_ref = int(state["focus_ref"])
            temp_ref = float(state["temp_ref"])
            tcf = float(state["model_tcf"])

    try:
        current_position = read_position(focuser)
    except RuntimeError as exc:
        log.error(str(exc))
        log.info("END   | pos=N/A | reason=error")
        disconnect_focuser(focuser, log)
        return 1

    try:
        current_temperature = (
            args.temp if args.temp is not None else read_temperature(focuser)
        )
    except RuntimeError as exc:
        log.error(str(exc))
        log.info(f"END   | pos={current_position} | reason=error")
        disconnect_focuser(focuser, log)
        return 1

    delta_temperature = current_temperature - temp_ref
    base_focus_target = round(focus_ref + tcf * delta_temperature)
    requested_focus_target = base_focus_target + selected_filter.offset_steps

    minimum_position, maximum_position = get_focuser_limits(focuser)
    focus_target, target_was_clamped = clamp_target_to_limits(
        requested_focus_target,
        minimum_position,
        maximum_position,
    )

    if target_was_clamped:
        maximum_text = str(maximum_position) if maximum_position is not None else "unknown"
        log.warning(
            f"Requested target={requested_focus_target} is outside ASCOM limits "
            f"[{minimum_position}, {maximum_text}]; using clamped target={focus_target}"
        )

    correction = focus_target - current_position
    needs_backlash = args.backlash > 0 and focus_target < current_position

    calculation_text = (
        f"T={current_temperature:.2f}{DEG_C} | "
        f"{DELTA}T={delta_temperature:+.2f}{DEG_C} | "
        f"TCF={tcf:.2f} | "
        f"filter={selected_filter.position} ({selected_filter.name}) | "
        f"filter_offset={selected_filter.offset_steps:+d} | "
        f"base_target={base_focus_target} | "
        f"target={focus_target} | "
        f"pos={current_position} | "
        f"correction={correction:+d}"
    )

    if correction == 0:
        log.info(
            f"{calculation_text} | backlash=False | final={current_position} | "
            "no move needed"
        )
        log.info(f"END   | pos={current_position} | reason=ok")
        disconnect_focuser(focuser, log)
        return 0

    if needs_backlash and abs(correction) < args.min_correction:
        log.warning(
            f"{calculation_text} | below min_correction={args.min_correction} "
            "(backlash direction) — skipped"
        )
        log.info(f"END   | pos={current_position} | reason=min_correction")
        disconnect_focuser(focuser, log)
        return 0

    if args.dry_run:
        log.info(
            f"DRY | {calculation_text} | backlash={needs_backlash} | "
            "move NOT executed"
        )
        log.info(f"END   | pos={current_position} | reason=dry_run")
        disconnect_focuser(focuser, log)
        return 0

    try:
        final_position = move_focuser_with_backlash(
            focuser,
            focus_target,
            current_position,
            args.backlash,
            args.move_timeout,
        )
    except Exception as exc:
        log.error(
            f"{calculation_text} | move failed: {exc}"
        )
        log.info(f"END   | pos={current_position} | reason=error")
        disconnect_focuser(focuser, log)
        return 1

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

    try:
        save_state(state, state_json_path)
    except OSError as exc:
        log.error(f"Could not update state JSON {state_json_path}: {exc}")
        log.info(f"END   | pos={final_position} | reason=state_save_error")
        disconnect_focuser(focuser, log)
        return 1

    log.info(f"END   | pos={final_position} | reason={end_reason}")
    disconnect_focuser(focuser, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())