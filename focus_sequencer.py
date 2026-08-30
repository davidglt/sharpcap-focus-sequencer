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

Formula
-------
    focus_target = focus_ref + TCF * (T_current - T_ref)

Where:
    focus_ref  = focuser position at the reference autofocus point
    T_ref      = temperature at the reference autofocus point
    T_current  = current temperature read from the EAF sensor
    TCF        = temperature compensation factor (steps / ºC)

Backlash compensation
---------------------
    To eliminate backlash, the focuser always arrives at the target
    from below (increasing step numbers = outward direction on C8 + F/6.3).
    If the target is below the current position, the script first moves
    to (target - backlash_steps) and then moves up to the target.
    The backlash value is configurable via --backlash (default: 500 steps).
    Set to 0 to disable backlash compensation entirely.

Minimum correction threshold
-----------------------------
    The --min-correction threshold applies ONLY when a backlash overshoot
    would be needed (target < current_position). In that case, a small
    correction is not worth the cost of a double move (overshoot + return).
    When no backlash is needed (target >= current_position), the script
    always moves, even if the correction is tiny, to keep the focus
    continuously well-corrected without accumulating drift.
    With TCF = -61.59 steps/ºC, 50 steps corresponds to ~0.81 ºC.
    Set to 0 to always move regardless of correction size or direction.

Busy detection
--------------
    If the focuser is already moving when the script connects (e.g. SharpCap
    is running an autofocus), the script aborts immediately and exits cleanly.
    The PERIODIC ThermalCorrection will retry in 7 minutes.

ASCOM access
------------
    Direct ProgIDs (ZWO EAF driver, single-client only):
        ASCOM.EAF.Focuser    -> first EAF  (main tube:  C8  + ASI2600MC Pro, ~25 000 steps)
        ASCOM.EAF_2.Focuser  -> second EAF (guide tube: 50ED + ASI224MC,  ~335 000 steps)

    Main tube (C8 + ASI2600MC Pro):
        ASCOM Device Hub is required to allow simultaneous access from
        SharpCap and this script.  Configure Device Hub to proxy
        ASCOM.EAF.Focuser and point both clients to ASCOM.DeviceHub.Focuser.
        Use run_focus.bat (default ASCOM ID: ASCOM.DeviceHub.Focuser).

    Guide tube (50ED + ASI224MC):
        SharpCap does NOT access the guide tube EAF, so Device Hub is not
        needed.  The script connects directly to ASCOM.EAF_2.Focuser.
        Use run_focus_guide.bat (passes --ascom-id ASCOM.EAF_2.Focuser
        and --state-json pointing to sharpcap_focus_state_guide.json).

State JSON
----------
    Both the state JSON and the producer script live exclusively in the
    sibling repository sharpcap-focus-temperature:

        ..\sharpcap-focus-temperature\sharpcap_focus_state.json        (main tube)
        ..\sharpcap-focus-temperature\sharpcap_focus_state_guide.json  (guide tube)
        ..\sharpcap-focus-temperature\sharpcap_focuser.py

    Neither file must be copied into this repository.
    Use --state-json only in exceptional cases (e.g. a custom clone layout).

    At the beginning of each cycle (after the busy check passes),
    focus_sequencer.py calls sharpcap_focuser.py to regenerate the
    state JSON from the latest SharpCap log files.  This ensures that
    any autofocus triggered by SharpCap during the session (e.g. a
    PERIODIC Refocus WHEN TEMP CHANGES BY 1) is immediately reflected
    in the thermal model before the next thermal correction is applied.

    The --output-state-json argument is always passed explicitly so the
    producer writes to the exact file the sequencer is already reading.

    The --tube argument is passed to sharpcap_focuser.py based on the
    state JSON filename: if the filename contains "guide", --tube guide
    is added; otherwise --tube main is used.  This ensures the correct
    focuser position range (330 000–370 000 steps for the 50ED, 24 000–
    27 000 steps for the C8) is applied when filtering log entries.

    If the refresh fails (script not found, non-zero exit code) the
    sequencer logs UPDATE FAILED and continues with the previously
    loaded state rather than aborting.

    Note: in --dry-run mode the state JSON refresh is skipped.  This
    means a dry run may use a model that is slightly older than the
    current session would produce.  For a fully up-to-date simulation
    run sharpcap_focuser.py manually first.

    sharpcap_focuser.py is invoked using the Python interpreter from the
    sibling repository's own virtual environment (.venv), not the
    sequencer's .venv.  This is required because sharpcap_focuser.py
    depends on numpy, statsmodels, and matplotlib, which are installed
    only in the sharpcap-focus-temperature .venv.
    See resolve_producer_python() for the resolution logic.

    The subprocess is launched with cwd set to the sibling repository root
    so that relative paths inside sharpcap_focuser.py resolve correctly
    regardless of where focus_sequencer.py is called from.

Dry-run mode
------------
    In --dry-run mode the script connects to the ASCOM driver to read the
    real focuser position and temperature, but does NOT move the focuser,
    does NOT update the state JSON, and does NOT refresh the state JSON
    from SharpCap logs.  This gives an accurate simulation of what the
    live run would do based on the current (possibly slightly stale) model.
    Supply --temp to override the temperature read from the driver
    (useful when the EAF sensor is not connected).

Logging
-------
    Each run appends to logs/YYYYMMDD_focus_sequencer.log (one file per day).
    The logs/ directory is created automatically if it does not exist.
    Log files are not committed to the repository (.gitignore).

    Every execution produces exactly one START line and one END line,
    with zero or more INFO/SKIP/ERROR lines in between.

    Log line format:
        YYYY-MM-DD HH:MM:SS | LEVEL | <message>

    Levels used:
        START  — beginning of each execution; reference data from state JSON
                 (last_focus and last_T are the values applied in the previous
                 run, not an estimate of the current state)
        INFO   — successful correction with full details, or no move needed
        SKIP   — correction skipped (focuser busy or below min-correction)
        ERROR  — unexpected error (ASCOM failure, move timeout, sensor error)
        DRY    — dry-run execution (no move performed)
        END    — end of each execution; always written regardless of outcome
                 pos=N/A when focuser could not be connected

    Example session (successful correction):
        2026-08-25 23:14:00 | START | ref=2026-08-25T21:30:00 | focus_ref=24831 | T_ref=18.50ºC | TCF=-61.59 | last_focus=24831 | last_T=18.50ºC | backlash=500 | min_correction=50
        2026-08-25 23:14:01 | INFO  | UPDATE OK — ref=2026-08-25 23:11:32 | focus_ref=25342 | T_ref=18.40ºC | TCF=-61.59
        2026-08-25 23:14:02 | INFO  | T=17.20ºC | dT=-1.30ºC | TCF=-61.59 | pos=24831 | correction=+80 | backlash=False | final=24911
        2026-08-25 23:14:02 | END   | pos=24911 | reason=ok

    Example session (focuser busy):
        2026-08-25 23:21:00 | START | ...
        2026-08-25 23:21:01 | SKIP  | Focuser busy (IsMoving=True) — skipping this cycle, retry in 7 min
        2026-08-25 23:21:01 | END   | pos=24911 | reason=busy

    Example session (state refresh failed, correction continues with previous state):
        2026-08-25 23:28:00 | START | ...
        2026-08-25 23:28:01 | ERROR | UPDATE FAILED (rc=1): <stderr from sharpcap_focuser.py>
        2026-08-25 23:28:03 | INFO  | T=17.10ºC | dT=-1.40ºC | correction=+12 | ...
        2026-08-25 23:28:03 | END   | pos=24923 | reason=ok

    Example session (below min-correction):
        2026-08-25 23:28:00 | START | ...
        2026-08-25 23:28:02 | SKIP  | T=17.10ºC | dT=-1.40ºC | correction=+12 | below min_correction=50 (backlash direction) — skipped
        2026-08-25 23:28:02 | END   | pos=24911 | reason=min_correction

    Example session (ASCOM connection failure):
        2026-08-25 23:35:00 | START | ...
        2026-08-25 23:35:01 | ERROR | ASCOM connection failed: No such device 'ASCOM.DeviceHub.Focuser'
        2026-08-25 23:35:01 | END   | pos=N/A | reason=error

    Example session (move timeout):
        2026-08-25 23:42:00 | START | ...
        2026-08-25 23:43:02 | ERROR | T=17.00ºC | pos=24911 | target=24991 | move failed: Focuser did not reach position 24991 within 60s
        2026-08-25 23:43:02 | END   | pos=24911 | reason=error

    Example session (temperature sensor disconnected):
        2026-08-25 23:49:00 | START | ...
        2026-08-25 23:49:01 | ERROR | Could not read temperature: Temperature returned None — check EAF sensor
        2026-08-25 23:49:01 | END   | pos=24911 | reason=error

Installation
------------
    Both repositories must be cloned as sibling directories under the same
    parent folder. The exact parent path does not matter; only the sibling
    relationship is required:

        <any-parent>/
            sharpcap-focus-sequencer/   <- this repo
            sharpcap-focus-temperature/ <- sibling repo

    Example:
        C:\astro\sharpcap-focus-sequencer\
        C:\astro\sharpcap-focus-temperature\

    Each repository must have its own .venv created and populated:
        cd sharpcap-focus-sequencer  && python -m venv .venv && .venv\Scripts\pip install -r requirements\requirements.txt
        cd sharpcap-focus-temperature && python -m venv .venv && .venv\Scripts\pip install -r requirements\requirements.txt

    To launch the sequencer use the provided wrappers:
        run_focus.bat [args]               <- main tube  (C8, via Device Hub)
        run_focus_guide.bat [args]         <- guide tube (50ED, direct ASCOM)

Usage
-----
    python focus_sequencer.py
    python focus_sequencer.py --state-json path/to/sharpcap_focus_state.json
    python focus_sequencer.py --ascom-id "ASCOM.DeviceHub.Focuser"
    python focus_sequencer.py --ascom-id "ASCOM.EAF_2.Focuser"
    python focus_sequencer.py --dry-run
    python focus_sequencer.py --dry-run --temp 18.5
    python focus_sequencer.py --backlash 500
    python focus_sequencer.py --backlash 0        # disable backlash compensation
    python focus_sequencer.py --min-correction 50 # backlash overshoot threshold
    python focus_sequencer.py --min-correction 0  # always move regardless of direction

Author
------
David González López-Tercero

Contact
-------
Email: davidglt@dragonit.es
Website: https://dragonit.es

Date
----
2026-08-30

License
-------
GPL-3.0-or-later
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DEG_C = "ºC"  # masculine ordinal (U+00BA) — safe in Windows cp1252 console
DELTA = "d"  # plain ASCII prefix for delta (dT instead of \u0394T)
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

# Custom log levels so the log file shows fixed-width labels.
# START (25) sits between DEBUG (10) and INFO (20)? No — we use a value
# above INFO so it is emitted at any standard log level ≥ INFO.
START_LEVEL = 25  # between INFO (20) and WARNING (30)
logging.addLevelName(START_LEVEL, "START")


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    """Configure and return the module logger.

    Appends to logs/YYYYMMDD_focus_sequencer.log (created if absent).
    Also streams to stdout so SharpCap's RUN output is visible.
    """
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)

    log_filename = log_dir / f"{datetime.now().strftime('%Y%m%d')}_focus_sequencer.log"

    fmt = "%(asctime)s | %(levelname)-5s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logger = logging.getLogger("focus_sequencer")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_filename, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logger.addHandler(ch)

    return logger


# Rename standard levels to fixed-width labels used in log lines.
logging.addLevelName(logging.INFO,    "INFO ")
logging.addLevelName(logging.WARNING, "SKIP ")
logging.addLevelName(logging.ERROR,   "ERROR")


def log_start(log: logging.Logger, msg: str) -> None:
    """Emit a START-level log line."""
    log.log(START_LEVEL, msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_producer_python(log: logging.Logger) -> str:
    """Return the path to the Python interpreter that should run sharpcap_focuser.py.

    sharpcap_focuser.py lives in the sibling repository sharpcap-focus-temperature
    and requires numpy, statsmodels, and matplotlib — packages that are installed
    only in *that* repository's virtual environment, not in the sequencer's .venv.

    Resolution order (first existing path wins):
        1. <sibling-repo>/.venv/Scripts/python.exe  (Windows)
        2. <sibling-repo>/.venv/bin/python           (POSIX / WSL)

    If neither candidate exists the function falls back to sys.executable and
    logs a warning.  This covers the edge case where the sibling repo has not
    yet had its .venv created (first-run scenario).
    """
    sibling_root = SHARPCAP_FOCUSER_PATH.parent
    candidates = [
        sibling_root / ".venv" / "Scripts" / "python.exe",  # Windows
        sibling_root / ".venv" / "bin" / "python",           # POSIX / WSL
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
    """Return the state JSON path to use.

    Uses --state-json if supplied (exceptional cases only).
    Otherwise always returns the canonical sibling-repo path.
    Raises FileNotFoundError if the resolved path does not exist.
    """
    if cli_path is not None:
        p = Path(cli_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"--state-json path not found: {p}")
        return p
    if not STATE_JSON_PATH.exists():
        raise FileNotFoundError(
            f"sharpcap_focus_state.json not found at: {STATE_JSON_PATH}\n"
            "Run sharpcap_focuser.py (sharpcap-focus-temperature) first to generate it."
        )
    return STATE_JSON_PATH


def refresh_state_json(state_json_path: Path, log: logging.Logger) -> dict | None:
    """Call sharpcap_focuser.py to regenerate the state JSON from the latest
    SharpCap logs.  Returns the freshly loaded state dict on success, or
    None if the refresh could not be completed (the caller should continue
    with the previously loaded state).

    Not called in --dry-run mode; see the Dry-run mode section in the
    module docstring for implications.

    The producer script is located exclusively in the sibling repository
    sharpcap-focus-temperature and must NOT be copied into this repo.

    The sibling repository's .venv Python is used (resolve_producer_python)
    so that numpy/statsmodels/matplotlib are available to sharpcap_focuser.py.

    cwd is set to the sibling repository root so that all relative paths
    inside sharpcap_focuser.py resolve correctly regardless of the working
    directory from which focus_sequencer.py was launched.

    The --tube argument is derived from the state JSON filename:
        - filename contains "guide"  →  --tube guide  (50ED, 330 000–370 000 steps)
        - otherwise                  →  --tube main   (C8,   24 000–27 000 steps)
    This ensures the correct focuser position range is used when filtering
    SharpCap log entries, preventing the guide-tube JSON from being written
    with main-tube defaults (which would yield no matching entries and set
    model_tcf to null).
    """
    if not SHARPCAP_FOCUSER_PATH.exists():
        log.error(
            f"UPDATE FAILED — sharpcap_focuser.py not found at: {SHARPCAP_FOCUSER_PATH}"
        )
        return None

    producer_python = resolve_producer_python(log)
    sibling_root = str(SHARPCAP_FOCUSER_PATH.parent)

    # Detect tube from the state JSON filename so the correct position range
    # is used by sharpcap_focuser.py when filtering SharpCap log entries.
    tube = "guide" if "guide" in state_json_path.name.lower() else "main"

    result = subprocess.run(
        [
            producer_python,
            str(SHARPCAP_FOCUSER_PATH),
            "--tube", tube,
            "--output-state-json", str(state_json_path),
        ],
        capture_output=True,
        text=True,
        cwd=sibling_root,
    )

    if result.returncode != 0:
        stderr = result.stderr.strip().replace("\n", " ") or "(no stderr)"
        log.error(f"UPDATE FAILED (rc={result.returncode}): {stderr}")
        return None

    try:
        fresh_state = load_state(state_json_path)
    except (FileNotFoundError, ValueError) as exc:
        log.error(f"UPDATE FAILED — could not reload state JSON after refresh: {exc}")
        return None

    ref       = fresh_state.get("timestamp_ref", "unknown")
    focus_ref = fresh_state.get("focus_ref", "?")
    temp_ref  = fresh_state.get("temp_ref", "?")
    tcf       = fresh_state.get("model_tcf", "?")
    temp_str  = f"{temp_ref:.2f}{DEG_C}" if isinstance(temp_ref, float) else str(temp_ref)
    tcf_str   = f"{tcf:.2f}" if isinstance(tcf, float) else str(tcf)
    log.info(f"UPDATE OK — ref={ref} | focus_ref={focus_ref} | T_ref={temp_str} | TCF={tcf_str}")
    return fresh_state


def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "On-demand thermal focus compensator for ZWO EAF via ASCOM. "
            "Reads the model from sharpcap_focus_state.json and moves the "
            "focuser to the thermally corrected position."
        )
    )
    parser.add_argument("--state-json", default=None)
    parser.add_argument("--ascom-id", default=DEFAULT_ASCOM_ID)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--temp", type=float, default=None, metavar="DEGREES")
    parser.add_argument("--backlash", type=int, default=DEFAULT_BACKLASH_STEPS, metavar="STEPS")
    parser.add_argument("--min-correction", type=int, default=DEFAULT_MIN_CORRECTION, metavar="STEPS")
    parser.add_argument("--move-timeout", type=float, default=MOVE_TIMEOUT_S)
    return parser.parse_args()


def load_state(state_json_path: Path) -> dict:
    with state_json_path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    required = ["focus_ref", "temp_ref", "model_tcf", "last_temp_applied", "last_focus_applied"]
    missing = [k for k in required if k not in state]
    if missing:
        raise ValueError(f"State JSON is missing required fields: {missing}")
    if state["model_tcf"] is None:
        raise ValueError("model_tcf is null in the state JSON.")
    return state


def connect_focuser(ascom_id: str):
    try:
        import win32com.client
    except ImportError:
        raise ImportError("pywin32 is required. Install with: pip install pywin32")
    focuser = win32com.client.Dispatch(ascom_id)
    focuser.Connected = True
    if not focuser.Connected:
        raise RuntimeError(f"Could not connect to ASCOM focuser: {ascom_id}")
    return focuser


def check_not_busy(focuser) -> bool:
    """Return True if the focuser is ready (not moving).

    Raises RuntimeError if IsMoving cannot be read, so the caller can
    distinguish a genuine busy state from a driver/connection failure.
    A connection failure should be treated as an error, not silently
    skipped as if the focuser were merely busy.
    """
    try:
        return not focuser.IsMoving
    except Exception as exc:
        raise RuntimeError(f"Could not read IsMoving from focuser: {exc}") from exc


def read_temperature(focuser) -> float:
    try:
        temp = focuser.Temperature
    except Exception as exc:
        raise RuntimeError(f"Could not read temperature: {exc}") from exc
    if temp is None:
        raise RuntimeError("Focuser returned None for Temperature — check EAF sensor.")
    return float(temp)


def read_position(focuser) -> int:
    return int(focuser.Position)


def move_focuser(focuser, target: int, timeout_s: float) -> int:
    focuser.Move(target)
    deadline = time.monotonic() + timeout_s
    while focuser.IsMoving:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Focuser did not reach position {target} within {timeout_s:.0f} s.")
        time.sleep(MOVE_POLL_INTERVAL_S)
    return int(focuser.Position)


def move_focuser_with_backlash(
    focuser, target: int, current_position: int, backlash_steps: int, timeout_s: float
) -> int:
    if backlash_steps > 0 and target < current_position:
        overshoot = max(target - backlash_steps, 0)
        move_focuser(focuser, overshoot, timeout_s)
    return move_focuser(focuser, target, timeout_s)


def save_state(state: dict, state_json_path: Path) -> None:
    with state_json_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log = setup_logging()
    args = parse_arguments()

    # --- Load state ---
    try:
        state_json_path = resolve_state_json(args.state_json)
        state = load_state(state_json_path)
    except (FileNotFoundError, ValueError) as exc:
        log.error(str(exc))
        log.info("END   | pos=N/A | reason=error")
        sys.exit(1)

    focus_ref     = int(state["focus_ref"])
    temp_ref      = float(state["temp_ref"])
    tcf           = float(state["model_tcf"])
    timestamp_ref = state.get("timestamp_ref", "unknown")
    last_focus    = state.get("last_focus_applied", "unknown")
    last_t        = state.get("last_temp_applied", "unknown")

    last_t_str = f"{last_t:.2f}{DEG_C}" if isinstance(last_t, float) else str(last_t)

    log_start(
        log,
        f"ref={timestamp_ref} | focus_ref={focus_ref} | "
        f"T_ref={temp_ref:.2f}{DEG_C} | TCF={tcf:.2f} | "
        f"last_focus={last_focus} | last_T={last_t_str} | "
        f"backlash={args.backlash} | min_correction={args.min_correction}"
        + (" | DRY_RUN" if args.dry_run else "")
    )

    # --- Connect ---
    focuser = None
    try:
        focuser = connect_focuser(args.ascom_id)
    except Exception as exc:
        log.error(f"ASCOM connection failed: {exc}")
        log.info("END   | pos=N/A | reason=error")
        sys.exit(1)

    # --- Busy check ---
    try:
        ready = check_not_busy(focuser)
    except RuntimeError as exc:
        log.error(f"Focuser busy check failed: {exc}")
        log.info("END   | pos=N/A | reason=error")
        focuser.Connected = False
        sys.exit(1)

    if not ready:
        current_position = read_position(focuser)
        log.warning("Focuser busy (IsMoving=True) — skipping this cycle, retry in 7 min")
        log.info(f"END   | pos={current_position} | reason=busy")
        focuser.Connected = False
        return

    # --- Refresh state JSON from latest SharpCap logs ---
    # Always called after the busy check so we never interrupt an ongoing
    # autofocus.  The producer writes to the exact same path the sequencer
    # is reading (--output-state-json passed explicitly) so there is a
    # single source of truth in ../sharpcap-focus-temperature/.
    # The --tube argument is derived from the state JSON filename so the
    # correct position range is applied (guide: 330 000–370 000 steps,
    # main: 24 000–27 000 steps).
    # On failure we log and continue with the previously loaded state.
    # Skipped in --dry-run mode (see module docstring).
    if not args.dry_run:
        fresh = refresh_state_json(state_json_path, log)
        if fresh is not None:
            state     = fresh
            focus_ref = int(state["focus_ref"])
            temp_ref  = float(state["temp_ref"])
            tcf       = float(state["model_tcf"])

    current_position = read_position(focuser)

    # --- Temperature ---
    try:
        t_current = args.temp if args.temp is not None else read_temperature(focuser)
    except RuntimeError as exc:
        log.error(str(exc))
        log.info(f"END   | pos={current_position} | reason=error")
        focuser.Connected = False
        sys.exit(1)

    # --- Calculate correction ---
    delta_t        = t_current - temp_ref
    focus_target   = round(focus_ref + tcf * delta_t)
    correction     = focus_target - current_position
    needs_backlash = args.backlash > 0 and focus_target < current_position

    # --- No correction needed ---
    if correction == 0:
        log.info(
            f"T={t_current:.2f}{DEG_C} | {DELTA}T={delta_t:+.2f}{DEG_C} | TCF={tcf:.2f} | "
            f"pos={current_position} | correction=0 | backlash=False | final={current_position} | no move needed"
        )
        log.info(f"END   | pos={current_position} | reason=ok")
        focuser.Connected = False
        return

    # --- Below min-correction threshold (backlash direction only) ---
    if needs_backlash and abs(correction) < args.min_correction:
        log.warning(
            f"T={t_current:.2f}{DEG_C} | {DELTA}T={delta_t:+.2f}{DEG_C} | TCF={tcf:.2f} | "
            f"pos={current_position} | correction={correction:+d} | "
            f"below min_correction={args.min_correction} (backlash direction) — skipped"
        )
        log.info(f"END   | pos={current_position} | reason=min_correction")
        focuser.Connected = False
        return

    # --- Dry run ---
    if args.dry_run:
        log.info(
            f"DRY | T={t_current:.2f}{DEG_C} | {DELTA}T={delta_t:+.2f}{DEG_C} | TCF={tcf:.2f} | "
            f"pos={current_position} | correction={correction:+d} | "
            f"backlash={needs_backlash} | target={focus_target} | move NOT executed"
        )
        log.info(f"END   | pos={current_position} | reason=dry_run")
        focuser.Connected = False
        return

    # --- Apply correction ---
    try:
        final_position = move_focuser_with_backlash(
            focuser, focus_target, current_position, args.backlash, args.move_timeout
        )
    except (TimeoutError, Exception) as exc:
        log.error(
            f"T={t_current:.2f}{DEG_C} | pos={current_position} | "
            f"target={focus_target} | move failed: {exc}"
        )
        log.info(f"END   | pos={current_position} | reason=error")
        focuser.Connected = False
        sys.exit(1)

    if abs(final_position - focus_target) > 5:
        log.warning(
            f"T={t_current:.2f}{DEG_C} | {DELTA}T={delta_t:+.2f}{DEG_C} | TCF={tcf:.2f} | "
            f"pos={current_position} | correction={correction:+d} | backlash={needs_backlash} | "
            f"final={final_position} | WARNING: differs from target {focus_target} by more than 5 steps"
        )
        log.info(f"END   | pos={final_position} | reason=ok_with_warning")
    else:
        log.info(
            f"T={t_current:.2f}{DEG_C} | {DELTA}T={delta_t:+.2f}{DEG_C} | TCF={tcf:.2f} | "
            f"pos={current_position} | correction={correction:+d} | "
            f"backlash={needs_backlash} | final={final_position}"
        )
        log.info(f"END   | pos={final_position} | reason=ok")

    state["last_temp_applied"]  = round(t_current, 2)
    state["last_focus_applied"] = focus_target
    save_state(state, state_json_path)

    focuser.Connected = False


if __name__ == "__main__":
    main()
