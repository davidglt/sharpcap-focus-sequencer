#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 David González López-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
SharpCap Focus Sequencer — On-demand thermal focus compensator.

Reads the regression model and last autofocus reference produced by
sharpcap-focus-temperature, queries the current temperature from the ZWO EAF
external sensor via ASCOM, and moves the focuser to the thermally compensated
position.

The selected optical tube determines the state JSON, filter behavior and log:

    --tube main:
        Main tube: Celestron C8 + F/6.3 reducer + ASI2600MC Pro + ZWO EAF
        Daily log: logs\YYYYMMDD_focus_sequencer.log
        Supports filter offsets from focus_sequencer.properties.

    --tube guide:
        Guide tube: Sky-Watcher 50ED + ASI224MC + second ZWO EAF
        Daily log: logs\YYYYMMDD_focus_sequencer_guide.log
        Uses zero filter offset.

The main-tube thermal model must be calibrated with filter position 1,
"No filter". A selected capture filter contributes its configured focus offset:

    base_focus_target = focus_ref + TCF * (T_current - T_ref)
    final_focus_target = base_focus_target + filter_offset_steps

The guide tube uses its own thermal model and always applies zero filter
offset. Supplying --filter or --config for --tube guide is an error.

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

Usage
-----
    python focus_sequencer.py --tube main
    python focus_sequencer.py --tube main --filter 2
    python focus_sequencer.py --tube main --filter 2 --dry-run
    python focus_sequencer.py --tube guide
    python focus_sequencer.py --tube guide --state-json "..\sharpcap-focus-temperature\sharpcap_focus_state_guide.json"
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import math
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


DEG_C = "°C"
DELTA = "d"

MAIN_TUBE = "main"
GUIDE_TUBE = "guide"
TUBES = (MAIN_TUBE, GUIDE_TUBE)

STATE_JSON_FILENAME = "sharpcap_focus_state.json"
GUIDE_STATE_JSON_FILENAME = "sharpcap_focus_state_guide.json"

DEFAULT_ASCOM_ID = "ASCOM.DeviceHub.Focuser"
GUIDE_ASCOM_ID = "ASCOM.EAF_2.Focuser"
DEFAULT_BACKLASH_STEPS = 500
DEFAULT_MIN_CORRECTION = 50
MOVE_TIMEOUT_S = 60
MOVE_POLL_INTERVAL_S = 0.5
DEFAULT_REFRESH_TIMEOUT_S = 300

DEFAULT_CONFIG_FILENAME = "focus_sequencer.properties"
EXAMPLE_CONFIG_FILENAME = "focus_sequencer.properties.example"
MIN_FILTER_POSITION = 1
MAX_FILTER_POSITION = 7

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
THERMAL_MODEL_DIRECTORY = SCRIPT_DIRECTORY.parent / "sharpcap-focus-temperature"

SHARPCAP_FOCUSER_PATH = THERMAL_MODEL_DIRECTORY / "sharpcap_focuser.py"
MAIN_STATE_JSON_PATH = THERMAL_MODEL_DIRECTORY / STATE_JSON_FILENAME
GUIDE_STATE_JSON_PATH = THERMAL_MODEL_DIRECTORY / GUIDE_STATE_JSON_FILENAME

START_LEVEL = 25
logging.addLevelName(START_LEVEL, "START")
logging.addLevelName(logging.INFO, "INFO ")
logging.addLevelName(logging.WARNING, "SKIP ")
logging.addLevelName(logging.ERROR, "ERROR")


class FocuserLockError(RuntimeError):
    """Raised when another run already owns the selected focuser lock."""


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

def log_filename_for_tube(tube: str) -> str:
    """Return the daily log filename for the selected optical tube."""
    date_prefix = datetime.now().strftime("%Y%m%d")

    if tube == GUIDE_TUBE:
        return f"{date_prefix}_focus_sequencer_guide.log"

    return f"{date_prefix}_focus_sequencer.log"


def setup_logging(tube: str) -> logging.Logger:
    """Create the daily file logger and a stdout handler."""
    log_dir = SCRIPT_DIRECTORY / "logs"
    log_dir.mkdir(exist_ok=True)

    log_path = log_dir / log_filename_for_tube(tube)

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


@contextlib.contextmanager
def focuser_execution_lock(ascom_id: str, log: logging.Logger) -> Iterator[None]:
    """Prevent overlapping sequencer runs from controlling the same ASCOM driver."""
    log_dir = SCRIPT_DIRECTORY / "logs"
    log_dir.mkdir(exist_ok=True)
    driver_key = hashlib.sha256(ascom_id.casefold().encode("utf-8")).hexdigest()[:16]
    lock_path = log_dir / f".focuser_{driver_key}.lock"

    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()

        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise FocuserLockError(
                    f"Another sequencer run is already using ASCOM focuser "
                    f"{ascom_id}."
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise FocuserLockError(
                    f"Another sequencer run is already using ASCOM focuser "
                    f"{ascom_id}."
                ) from exc

        try:
            yield
        finally:
            try:
                lock_file.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError as exc:
                log.error(f"Could not release focuser execution lock: {exc}")


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

    return SCRIPT_DIRECTORY / DEFAULT_CONFIG_FILENAME


# ---------------------------------------------------------------------------
# State and tube helpers
# ---------------------------------------------------------------------------

def default_state_json_path(tube: str) -> Path:
    """Return the default state JSON path for the selected tube."""
    return GUIDE_STATE_JSON_PATH if tube == GUIDE_TUBE else MAIN_STATE_JSON_PATH


def resolve_state_json(tube: str, cli_path: str | None) -> Path:
    """Return and validate the selected focus state JSON path."""
    state_path = (
        Path(cli_path).expanduser().resolve()
        if cli_path is not None
        else default_state_json_path(tube)
    )

    if not state_path.exists():
        if cli_path is not None:
            raise FileNotFoundError(f"--state-json path not found: {state_path}")

        raise FileNotFoundError(
            f"Focus state JSON not found for tube={tube}: {state_path}\n"
            "Run sharpcap_focuser.py in sharpcap-focus-temperature first."
        )

    filename_is_guide = GUIDE_TUBE in state_path.name.lower()

    if tube == GUIDE_TUBE and not filename_is_guide:
        raise ValueError(
            f"--tube guide requires a guide state JSON filename containing "
            f"'guide', got: {state_path.name}"
        )

    if tube == MAIN_TUBE and filename_is_guide:
        raise ValueError(
            f"--tube main cannot use a guide state JSON filename, got: "
            f"{state_path.name}"
        )

    return state_path


def load_state(state_json_path: Path) -> dict:
    """Read and validate the thermal-model state JSON."""
    try:
        with state_json_path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in state file {state_json_path}: {exc}") from exc

    if not isinstance(state, dict):
        raise ValueError(f"State JSON must contain an object: {state_json_path}")

    if state.get("valid") is False:
        reason = state.get("invalid_reason")
        detail = f" Reason: {reason}" if reason else ""
        raise ValueError(
            f"State JSON is marked invalid: {state_json_path}.{detail}"
        )

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

    for key in (
        "focus_ref",
        "temp_ref",
        "model_tcf",
        "last_temp_applied",
        "last_focus_applied",
    ):
        value = state[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a finite number in the state JSON.")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f"{key} must be a finite number in the state JSON.")

    for key in ("focus_ref", "last_focus_applied"):
        value = state[key]
        if value < 0 or int(value) != value:
            raise ValueError(
                f"{key} must be a non-negative integer in the state JSON."
            )

    return state


def resolve_producer_python(log: logging.Logger) -> str:
    """Return the sibling producer repository Python interpreter."""
    candidates = [
        THERMAL_MODEL_DIRECTORY / ".venv" / "Scripts" / "python.exe",
        THERMAL_MODEL_DIRECTORY / ".venv" / "bin" / "python",
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    log.warning(
        f"UPDATE — sibling .venv not found at "
        f"{THERMAL_MODEL_DIRECTORY / '.venv'}; falling back to sys.executable"
    )
    return sys.executable


def refresh_state_json(
    state_json_path: Path,
    tube: str,
    log: logging.Logger,
    timeout_s: float = DEFAULT_REFRESH_TIMEOUT_S,
) -> dict | None:
    """Refresh the selected state JSON from current SharpCap logs."""
    if not SHARPCAP_FOCUSER_PATH.exists():
        log.error(
            f"UPDATE FAILED — sharpcap_focuser.py not found at: "
            f"{SHARPCAP_FOCUSER_PATH}"
        )
        return None

    producer_python = resolve_producer_python(log)

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
            cwd=str(THERMAL_MODEL_DIRECTORY),
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        log.error(
            f"UPDATE FAILED — sharpcap_focuser.py exceeded the "
            f"{timeout_s:g} s refresh timeout."
        )
        return None
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

    temp_text = (
        f"{temp_ref:.2f}{DEG_C}"
        if isinstance(temp_ref, (int, float))
        else str(temp_ref)
    )
    tcf_text = f"{tcf:.2f}" if isinstance(tcf, (int, float)) else str(tcf)

    log.info(
        f"UPDATE OK — tube={tube} | ref={timestamp_ref} | "
        f"focus_ref={focus_ref} | T_ref={temp_text} | TCF={tcf_text}"
    )

    return fresh_state


def save_state(state: dict, state_json_path: Path) -> None:
    """Atomically persist runtime fields in the selected state JSON."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=state_json_path.parent,
            prefix=f".{state_json_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(state, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary_path, state_json_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


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

    try:
        temperature_value = float(temperature)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Could not parse focuser temperature: {exc}") from exc

    if not math.isfinite(temperature_value):
        raise RuntimeError("Focuser returned a non-finite temperature.")

    return temperature_value


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
    if not check_not_busy(focuser):
        raise RuntimeError(
            "Focuser became busy before the requested movement; aborting."
        )

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
        "--tube",
        choices=TUBES,
        default=MAIN_TUBE,
        help=(
            "Optical tube to correct. Determines default state JSON, filter "
            "behavior and daily log name (default: main)."
        ),
    )

    parser.add_argument(
        "--state-json",
        default=None,
        help=(
            "Path to the focus state JSON produced by sharpcap_focuser.py. "
            "Defaults depend on --tube."
        ),
    )

    parser.add_argument(
        "--ascom-id",
        default=None,
        help=(
            "ASCOM focuser ProgID. Defaults to the Device Hub for the main "
            f"tube ({DEFAULT_ASCOM_ID}) and the second ZWO EAF for the guide "
            f"tube ({GUIDE_ASCOM_ID})."
        ),
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

    parser.add_argument(
        "--refresh-timeout",
        type=float,
        default=DEFAULT_REFRESH_TIMEOUT_S,
        metavar="SECONDS",
        help=(
            "Maximum seconds to wait for the thermal-model refresh "
            f"(default: {DEFAULT_REFRESH_TIMEOUT_S})."
        ),
    )

    args = parser.parse_args()

    if args.backlash < 0:
        parser.error("--backlash must be zero or a positive integer.")

    if args.min_correction < 0:
        parser.error("--min-correction must be zero or a positive integer.")

    if args.move_timeout <= 0:
        parser.error("--move-timeout must be greater than zero.")

    if not math.isfinite(args.move_timeout):
        parser.error("--move-timeout must be a finite number.")

    if args.refresh_timeout <= 0 or not math.isfinite(args.refresh_timeout):
        parser.error("--refresh-timeout must be a finite number greater than zero.")

    if args.temp is not None and not math.isfinite(args.temp):
        parser.error("--temp must be a finite number.")

    if args.tube == GUIDE_TUBE and args.filter_position is not None:
        parser.error("--filter is supported only for --tube main.")

    if args.tube == GUIDE_TUBE and args.config is not None:
        parser.error("--config is supported only for --tube main.")

    if args.ascom_id is None:
        args.ascom_id = (
            GUIDE_ASCOM_ID if args.tube == GUIDE_TUBE else DEFAULT_ASCOM_ID
        )

    return args


# ---------------------------------------------------------------------------
# Main program
# ---------------------------------------------------------------------------

def select_active_filter(
    args: argparse.Namespace,
) -> tuple[ActiveFilter, SequencerConfig | None]:
    """Select the active filter context for the requested optical tube."""
    if args.tube == GUIDE_TUBE:
        return (
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
    args = parse_arguments()
    log = setup_logging(args.tube)
    try:
        with focuser_execution_lock(args.ascom_id, log):
            return _run_cycle(args, log)
    except FocuserLockError as exc:
        log.error(f"Could not start focus cycle: {exc}")
        log.info("END   | pos=N/A | reason=busy")
        return 1


def _run_cycle(args: argparse.Namespace, log: logging.Logger) -> int:
    focuser = None

    try:
        state_json_path = resolve_state_json(args.tube, args.state_json)
        active_filter, sequencer_config = select_active_filter(args)
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
        f"tube={args.tube} | ref={timestamp_ref} | focus_ref={focus_ref} | "
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
            fresh_state = refresh_state_json(
                state_json_path,
                args.tube,
                log,
                args.refresh_timeout,
            )

            if fresh_state is None:
                log.error(
                    "Thermal model refresh failed; refusing to use the "
                    "previous state."
                )
                log.info(
                    "END   | pos=N/A | reason=state_refresh_failed"
                )
                return 1

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
            f"tube={args.tube} | "
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
    raise SystemExit(main())