#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 David González López-Tercero <davidglt@dragonit.es>
# SPDX-License-Identifier: GPL-3.0-or-later

r"""
detect_focusers.py — ASCOM focuser detection utility.

Probes known ZWO EAF ASCOM ProgIDs and prints the Name, Description,
Position, and Temperature of each focuser that responds.

The ZWO EAF ASCOM driver registers each connected unit as:
    ASCOM.EAF.Focuser    (first unit)
    ASCOM.EAF_2.Focuser  (second unit)
    ASCOM.EAF_3.Focuser  (third unit)
    ...

Useful when multiple EAF units are connected (e.g. main tube + guide tube)
to identify which ProgID corresponds to each focuser before configuring
focus_sequencer.py.

Note: the ZWO EAF driver may need a short delay after connecting before
returning valid Position and Temperature values. If you see 0 / 0.00 for
a focuser that should have valid readings, run the script again with
--init-delay <seconds> (default: 1.0).

Usage
-----
    python detect_focusers.py
    python detect_focusers.py --init-delay 2.0

Expected output example (two EAF units connected)
-------------------------------------------------
    Probing ASCOM focuser ProgIDs...

    [OK] ASCOM.EAF.Focuser
         Name        : ZWO Focuser
         Description : ZWO Focuser (1)
         Position    : 312,540 steps   <-- guide tube (50ED + ASI224MC)
         Temperature : 18.40 ºC

    [OK] ASCOM.EAF_2.Focuser
         Name        : ZWO Focuser
         Description : ZWO Focuser (2)
         Position    : 25,041 steps    <-- main tube (C8 + ASI2600MC Pro)
         Temperature : 18.42 ºC

    [--] ASCOM.EAF_3.Focuser  ->  could not connect (COM error)

Author
------
David González López-Tercero

License
-------
GPL-3.0-or-later
"""

import argparse
import sys
import time

DEG_C = "ºC"  # masculine ordinal (U+00BA) — safe in Windows cp1252 console
DEFAULT_INIT_DELAY = 1.0

# Real ProgID scheme used by the ZWO EAF ASCOM driver:
#   First unit  : ASCOM.EAF.Focuser   (no number suffix)
#   Second unit : ASCOM.EAF_2.Focuser
#   Third unit  : ASCOM.EAF_3.Focuser  ... and so on
PROG_IDS = [
    "ASCOM.EAF.Focuser",
    "ASCOM.EAF_2.Focuser",
    "ASCOM.EAF_3.Focuser",
    "ASCOM.EAF_4.Focuser",
    "ASCOM.EAF_5.Focuser",
]


def probe_focuser(prog_id: str, init_delay: float) -> dict:
    """Try to connect to a focuser and return its properties, or an error dict."""
    try:
        import win32com.client
    except ImportError:
        print(
            "ERROR: pywin32 is not installed.\n"
            "Install it with: pip install pywin32"
        )
        sys.exit(1)

    try:
        focuser = win32com.client.Dispatch(prog_id)
        focuser.Connected = True

        if init_delay > 0:
            time.sleep(init_delay)

        name = getattr(focuser, "Name", "(unknown)")
        description = getattr(focuser, "Description", "(unknown)")
        position = getattr(focuser, "Position", None)
        temperature = getattr(focuser, "Temperature", None)

        focuser.Connected = False

        return {
            "name": name,
            "description": description,
            "position": int(position) if position is not None else None,
            "temperature": float(temperature) if temperature is not None else None,
        }

    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Probe ZWO EAF ASCOM ProgIDs and report connected focusers."
    )
    parser.add_argument(
        "--init-delay",
        type=float,
        default=DEFAULT_INIT_DELAY,
        metavar="SECONDS",
        help=(
            f"Seconds to wait after connecting before reading Position and "
            f"Temperature (default: {DEFAULT_INIT_DELAY}). Increase if values "
            f"appear as 0 / 0.00."
        ),
    )
    return parser.parse_args()


def main():
    """Probe all known ZWO EAF ProgIDs and report results."""
    args = parse_arguments()

    print("Probing ASCOM focuser ProgIDs...\n")

    found = []
    for prog_id in PROG_IDS:
        result = probe_focuser(prog_id, args.init_delay)

        if "error" in result:
            print(f"[--] {prog_id}  ->  could not connect ({result['error']})")
        else:
            found.append(prog_id)
            pos = result["position"]
            temp = result["temperature"]
            pos_str = f"{pos:,} steps" if pos is not None else "(unavailable)"
            temp_str = f"{temp:.2f} {DEG_C}" if temp is not None else "(unavailable)"

            warn = ""
            if pos == 0 and temp == 0.0:
                warn = (
                    "  \u26a0 position and temperature are both 0 — "
                    "driver may still be initialising; try --init-delay 3.0"
                )

            print(f"[OK] {prog_id}")
            print(f"     Name        : {result['name']}")
            print(f"     Description : {result['description']}")
            print(f"     Position    : {pos_str}")
            print(f"     Temperature : {temp_str}")
            if warn:
                print(f"     {warn}")
        print()

    if not found:
        print(
            "No focusers found. Make sure the ZWO EAF units are connected\n"
            "and the ASCOM driver is installed."
        )
        sys.exit(1)

    print("-" * 52)
    print(f"Found {len(found)} focuser(s): {', '.join(found)}")
    print()
    print("Tip: the main tube focuser (C8 + ASI2600MC Pro) should have")
    print("     a position around 25 000 steps -> ASCOM.EAF_2.Focuser")
    print("     Use its ProgID as --ascom-id in focus_sequencer.py.")


if __name__ == "__main__":
    main()
