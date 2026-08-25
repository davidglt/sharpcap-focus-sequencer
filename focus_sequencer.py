#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 David González López-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later

"""
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
    TCF        = temperature compensation factor (steps / °C)

Usage
-----
    python focus_sequencer.py
    python focus_sequencer.py --state-json path/to/sharpcap_focus_state.json
    python focus_sequencer.py --ascom-id "ASCOM.ZWO.Focuser"
    python focus_sequencer.py --dry-run

Author
------
David González López-Tercero

Contact
-------
Email: davidglt@dragonit.es
Website: https://dragonit.es

Date
----
2026-08-25

License
-------
GPL-3.0-or-later
"""

import argparse
import json
import sys
import time
from pathlib import Path

DEG_C = "\u00B0C"
DEFAULT_STATE_JSON = "sharpcap_focus_state.json"
DEFAULT_ASCOM_ID = "ASCOM.ZWO.Focuser"
MOVE_TIMEOUT_S = 60        # Maximum seconds to wait for focuser to reach target
MOVE_POLL_INTERVAL_S = 0.5 # Poll interval while waiting for move to complete


def parse_arguments():
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "On-demand thermal focus compensator for ZWO EAF via ASCOM. "
            "Reads the model from sharpcap_focus_state.json and moves the "
            "focuser to the thermally corrected position."
        )
    )
    parser.add_argument(
        "--state-json",
        default=DEFAULT_STATE_JSON,
        help=(
            f"Path to sharpcap_focus_state.json produced by "
            f"sharpcap-focus-temperature. Default: {DEFAULT_STATE_JSON}"
        ),
    )
    parser.add_argument(
        "--ascom-id",
        default=DEFAULT_ASCOM_ID,
        help=f"ASCOM ProgID of the focuser driver. Default: {DEFAULT_ASCOM_ID}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Calculate and print the target position without moving the "
            "focuser or writing the state JSON."
        ),
    )
    parser.add_argument(
        "--move-timeout",
        type=float,
        default=MOVE_TIMEOUT_S,
        help=f"Seconds to wait for the focuser move to complete. Default: {MOVE_TIMEOUT_S}",
    )
    return parser.parse_args()


def load_state(state_json_path: Path) -> dict:
    """Load and validate the focus state JSON."""
    if not state_json_path.exists():
        raise FileNotFoundError(
            f"State JSON not found: {state_json_path}\n"
            "Run sharpcap_focuser.py first to generate it."
        )

    with state_json_path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)

    required = [
        "focus_ref",
        "temp_ref",
        "model_tcf",
        "last_temp_applied",
        "last_focus_applied",
    ]
    missing = [k for k in required if k not in state]
    if missing:
        raise ValueError(
            f"State JSON is missing required fields: {missing}\n"
            "Re-run sharpcap_focuser.py to regenerate it."
        )

    if state["model_tcf"] is None:
        raise ValueError(
            "model_tcf is null in the state JSON — not enough autofocus points "
            "to fit the regression model. Collect more sessions and re-run "
            "sharpcap_focuser.py."
        )

    return state


def connect_focuser(ascom_id: str):
    """Connect to the ASCOM focuser and return the COM object."""
    try:
        import win32com.client  # pywin32
    except ImportError:
        raise ImportError(
            "pywin32 is required to use ASCOM on Windows.\n"
            "Install it with: pip install pywin32"
        )

    focuser = win32com.client.Dispatch(ascom_id)
    focuser.Connected = True

    if not focuser.Connected:
        raise RuntimeError(f"Could not connect to ASCOM focuser: {ascom_id}")

    return focuser


def read_temperature(focuser) -> float:
    """Read current temperature from the EAF external sensor via ASCOM."""
    try:
        temp = focuser.Temperature
    except Exception as exc:
        raise RuntimeError(
            f"Could not read temperature from focuser: {exc}\n"
            "Check that the ZWO EAF external sensor is connected."
        ) from exc

    if temp is None:
        raise RuntimeError(
            "Focuser returned None for Temperature. "
            "Check that the ZWO EAF external sensor is plugged in."
        )

    return float(temp)


def read_position(focuser) -> int:
    """Read current focuser position."""
    return int(focuser.Position)


def move_focuser(focuser, target: int, timeout_s: float) -> int:
    """Move focuser to target position and wait for completion."""
    focuser.Move(target)

    deadline = time.monotonic() + timeout_s
    while focuser.IsMoving:
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Focuser did not reach position {target} within "
                f"{timeout_s:.0f} s."
            )
        time.sleep(MOVE_POLL_INTERVAL_S)

    return int(focuser.Position)


def save_state(state: dict, state_json_path: Path) -> None:
    """Write the updated state back to the JSON file."""
    with state_json_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main():
    """Run the on-demand thermal focus compensation."""
    args = parse_arguments()
    state_json_path = Path(args.state_json).resolve()

    # --- Load model ---
    print(f"Loading state from: {state_json_path}")
    state = load_state(state_json_path)

    focus_ref = int(state["focus_ref"])
    temp_ref = float(state["temp_ref"])
    tcf = float(state["model_tcf"])
    last_temp = float(state["last_temp_applied"])
    last_focus = int(state["last_focus_applied"])
    timestamp_ref = state.get("timestamp_ref", "unknown")

    print(f"Reference autofocus  : {timestamp_ref}")
    print(f"Reference position   : {focus_ref} steps @ {temp_ref:.2f} {DEG_C}")
    print(f"Last applied         : {last_focus} steps @ {last_temp:.2f} {DEG_C}")
    print(f"TCF                  : {tcf:.2f} steps/{DEG_C}")

    # --- Connect and read temperature ---
    if args.dry_run:
        print("\n[DRY RUN] Skipping ASCOM connection.")
        print("Enter current temperature manually for simulation:")
        try:
            t_current = float(input(f"  Temperature ({DEG_C}): "))
        except (ValueError, EOFError):
            print("Invalid input. Aborting dry run.")
            sys.exit(1)
        current_position = focus_ref  # Assume at reference for dry run
    else:
        print(f"\nConnecting to ASCOM focuser: {args.ascom_id}")
        focuser = connect_focuser(args.ascom_id)
        print("Connected.")

        t_current = read_temperature(focuser)
        current_position = read_position(focuser)
        print(f"Current temperature  : {t_current:.2f} {DEG_C}")
        print(f"Current position     : {current_position} steps")

    # --- Calculate correction ---
    delta_t = t_current - temp_ref
    delta_steps = tcf * delta_t
    focus_target = round(focus_ref + delta_steps)
    correction = focus_target - (current_position if not args.dry_run else last_focus)

    print(f"\nDelta T (current - ref) : {delta_t:+.2f} {DEG_C}")
    print(f"Delta steps (TCF * dT)  : {delta_steps:+.1f}")
    print(f"Target position         : {focus_target} steps")
    print(f"Correction needed       : {correction:+d} steps")

    # --- Apply or report ---
    if args.dry_run:
        print("\n[DRY RUN] Move NOT executed.")
    else:
        if correction == 0:
            print("\nNo correction needed. Focuser already at target position.")
        else:
            print(f"\nMoving focuser to {focus_target} steps...")
            final_position = move_focuser(focuser, focus_target, args.move_timeout)
            print(f"Move complete. Final position: {final_position} steps")

            if abs(final_position - focus_target) > 5:
                print(
                    f"WARNING: Final position {final_position} differs from "
                    f"target {focus_target} by more than 5 steps."
                )

        # Update state JSON with current values
        state["last_temp_applied"] = round(t_current, 2)
        state["last_focus_applied"] = focus_target
        save_state(state, state_json_path)
        print(f"State JSON updated: {state_json_path}")

        focuser.Connected = False

    print("\nDone.")


if __name__ == "__main__":
    main()
