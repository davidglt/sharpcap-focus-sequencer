#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 David González López-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
ZWO EAF Backlash Measurement Tool.

Measures the mechanical backlash of the ZWO EAF focuser by commanding
the same target position from two opposite directions and comparing
the reported positions.

Method
------
Starting from a reference position P:

  1. Move to P + SWING (approach from below, inward direction)
  2. Move back to P from above  → record position A (approached from above)
  3. Move to P - SWING (approach from above, outward direction)
  4. Move back to P from below  → record position B (approached from below)
  5. backlash = |A - B|

Note
----
The ZWO EAF ASCOM driver typically reports the *commanded* position, not
the physical encoder position. If the driver has no encoder feedback,
both A and B will read identically and the result will be 0 steps.
In that case use the optical method (double V-curve in SharpCap) instead.

Usage
-----
    python measure_backlash.py
    python measure_backlash.py --ascom-id "ASCOM.EAF_2.Focuser"
    python measure_backlash.py --swing 2000
    python measure_backlash.py --repeats 5

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
import statistics
import time

DEFAULT_ASCOM_ID = "ASCOM.DeviceHub.Focuser"
DEFAULT_SWING = 2000       # steps to overshoot in each direction
DEFAULT_REPEATS = 3        # number of measurement cycles
MOVE_TIMEOUT_S = 120
MOVE_POLL_INTERVAL_S = 0.5


def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Measure ZWO EAF backlash by commanding the same position "
            "from two opposite directions and comparing reported positions."
        )
    )
    parser.add_argument(
        "--ascom-id",
        default=DEFAULT_ASCOM_ID,
        help=f"ASCOM ProgID of the focuser driver. Default: {DEFAULT_ASCOM_ID}",
    )
    parser.add_argument(
        "--swing",
        type=int,
        default=DEFAULT_SWING,
        metavar="STEPS",
        help=(
            f"Steps to move past the reference position in each direction "
            f"before returning. Larger values give cleaner results. "
            f"Default: {DEFAULT_SWING}"
        ),
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        metavar="N",
        help=f"Number of measurement cycles to average. Default: {DEFAULT_REPEATS}",
    )
    parser.add_argument(
        "--move-timeout",
        type=float,
        default=MOVE_TIMEOUT_S,
        help=f"Seconds to wait for each focuser move. Default: {MOVE_TIMEOUT_S}",
    )
    return parser.parse_args()


def connect_focuser(ascom_id: str):
    try:
        import win32com.client
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


def move_and_wait(focuser, target: int, timeout_s: float) -> int:
    """Move focuser to target and wait until IsMoving is False."""
    focuser.Move(target)
    deadline = time.monotonic() + timeout_s
    while focuser.IsMoving:
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Focuser did not reach position {target} within {timeout_s:.0f} s."
            )
        time.sleep(MOVE_POLL_INTERVAL_S)
    return int(focuser.Position)


def main():
    args = parse_arguments()

    print(f"Connecting to: {args.ascom_id}")
    focuser = connect_focuser(args.ascom_id)
    print("Connected.")

    ref = int(focuser.Position)
    swing = args.swing
    print(f"\nReference position : {ref} steps")
    print(f"Swing              : ±{swing} steps")
    print(f"Repeats            : {args.repeats}")

    # Safety check: ensure we have enough room to swing in both directions
    # (assumes minimum position is 0)
    if ref - swing < 0:
        suggested = ref // 2
        print(
            f"\nWARNING: ref - swing = {ref - swing} < 0. "
            f"Reduce --swing to at most {suggested} or move focuser outward first."
        )
        focuser.Connected = False
        return

    backlash_samples = []

    for i in range(1, args.repeats + 1):
        print(f"\n--- Cycle {i}/{args.repeats} ---")

        # Step 1: move to ref + swing (come from below, go above ref)
        target_high = ref + swing
        print(f"  Moving to {target_high} (above ref)...")
        move_and_wait(focuser, target_high, args.move_timeout)

        # Step 2: move back to ref from above  → position A
        print(f"  Moving back to ref {ref} from above...")
        pos_a = move_and_wait(focuser, ref, args.move_timeout)
        print(f"  Position A (from above) = {pos_a}")

        # Step 3: move to ref - swing (go below ref)
        target_low = ref - swing
        print(f"  Moving to {target_low} (below ref)...")
        move_and_wait(focuser, target_low, args.move_timeout)

        # Step 4: move back to ref from below  → position B
        print(f"  Moving back to ref {ref} from below...")
        pos_b = move_and_wait(focuser, ref, args.move_timeout)
        print(f"  Position B (from below) = {pos_b}")

        backlash = abs(pos_a - pos_b)
        backlash_samples.append(backlash)
        print(f"  Backlash this cycle     = {backlash} steps")

    # Return to reference
    print(f"\nReturning to reference position {ref}...")
    move_and_wait(focuser, ref, args.move_timeout)

    focuser.Connected = False

    # --- Results ---
    print("\n" + "=" * 40)
    print("BACKLASH MEASUREMENT RESULTS")
    print("=" * 40)
    for idx, s in enumerate(backlash_samples, 1):
        print(f"  Cycle {idx}: {s} steps")
    print("-" * 40)
    mean_bl = statistics.mean(backlash_samples)
    print(f"  Mean    : {mean_bl:.1f} steps")
    if len(backlash_samples) > 1:
        stdev_bl = statistics.stdev(backlash_samples)
        print(f"  Std dev : {stdev_bl:.1f} steps")
    print("=" * 40)

    if mean_bl == 0:
        print(
            "\nResult is 0 — the driver likely reports commanded position, not "
            "physical position. Use the optical double V-curve method in SharpCap "
            "to measure real mechanical backlash."
        )
    else:
        print(
            f"\nSuggested --backlash value for focus_sequencer.py: {round(mean_bl)}"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
