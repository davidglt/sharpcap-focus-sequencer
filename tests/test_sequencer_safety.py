import contextlib
import io
import sys
import types
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import detect_focusers
import focus_sequencer


class SequencerSafetyTests(unittest.TestCase):
    def test_tube_selects_the_matching_default_ascom_driver(self):
        for tube, expected in (
            ("main", focus_sequencer.DEFAULT_ASCOM_ID),
            ("guide", focus_sequencer.GUIDE_ASCOM_ID),
        ):
            with self.subTest(tube=tube), mock.patch.object(
                sys,
                "argv",
                ["focus_sequencer.py", "--tube", tube],
            ):
                args = focus_sequencer.parse_arguments()

            self.assertEqual(args.ascom_id, expected)

    def test_explicit_ascom_driver_overrides_tube_default(self):
        with mock.patch.object(
            sys,
            "argv",
            [
                "focus_sequencer.py",
                "--tube",
                "guide",
                "--ascom-id",
                "ASCOM.Custom.Focuser",
            ],
        ):
            args = focus_sequencer.parse_arguments()

        self.assertEqual(args.ascom_id, "ASCOM.Custom.Focuser")

    def test_move_timeout_rejects_non_finite_values(self):
        for value in ("nan", "inf", "-inf"):
            with (
                self.subTest(value=value),
                mock.patch.object(
                    sys,
                    "argv",
                    ["focus_sequencer.py", f"--move-timeout={value}"],
                ),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit):
                    focus_sequencer.parse_arguments()

    def test_refresh_failure_aborts_without_using_loaded_state(self):
        args = Namespace(
            tube="main",
            state_json=None,
            ascom_id=focus_sequencer.DEFAULT_ASCOM_ID,
            backlash=500,
            min_correction=50,
            dry_run=False,
            temp=18.0,
            move_timeout=60.0,
            filter_position=None,
            config=None,
        )
        active_filter = focus_sequencer.ActiveFilter(
            position=1,
            name="No filter",
            offset_steps=0,
            applies_to_main_tube=True,
        )
        focuser = mock.Mock()
        logger = mock.Mock()

        with (
            mock.patch.object(focus_sequencer, "parse_arguments", return_value=args),
            mock.patch.object(focus_sequencer, "setup_logging", return_value=logger),
            mock.patch.object(
                focus_sequencer,
                "resolve_state_json",
                return_value=Path("state.json"),
            ),
            mock.patch.object(
                focus_sequencer,
                "select_active_filter",
                return_value=(active_filter, None),
            ),
            mock.patch.object(
                focus_sequencer,
                "load_state",
                return_value={
                    "focus_ref": 10000,
                    "temp_ref": 18.0,
                    "model_tcf": -60.0,
                    "last_temp_applied": 18.0,
                    "last_focus_applied": 10000,
                },
            ),
            mock.patch.object(focus_sequencer, "connect_focuser", return_value=focuser),
            mock.patch.object(focus_sequencer, "check_not_busy", return_value=True),
            mock.patch.object(focus_sequencer, "refresh_state_json", return_value=None),
            mock.patch.object(focus_sequencer, "disconnect_focuser") as disconnect,
            mock.patch.object(focus_sequencer, "read_position") as read_position,
        ):
            result = focus_sequencer.main()

        self.assertEqual(result, 1)
        read_position.assert_not_called()
        focuser.Move.assert_not_called()
        disconnect.assert_called_once_with(focuser, logger)
        logger.info.assert_any_call(
            "END   | pos=N/A | reason=state_refresh_failed"
        )

    def test_detector_disconnects_after_property_read_failure(self):
        class FakeFocuser:
            def __init__(self):
                self.connected = False

            @property
            def Connected(self):
                return self.connected

            @Connected.setter
            def Connected(self, value):
                self.connected = value

            Name = "ZWO Focuser"
            Description = "Test EAF"

            @property
            def Position(self):
                raise RuntimeError("Position unavailable")

            Temperature = 18.0

        focuser = FakeFocuser()
        client = types.ModuleType("win32com.client")
        client.Dispatch = mock.Mock(return_value=focuser)
        win32com = types.ModuleType("win32com")
        win32com.client = client

        with mock.patch.dict(
            sys.modules,
            {"win32com": win32com, "win32com.client": client},
        ):
            result = detect_focusers.probe_focuser("ASCOM.EAF.Focuser", 0)

        self.assertIn("error", result)
        self.assertFalse(focuser.Connected)

    def test_detector_reports_disconnect_failure(self):
        class FakeFocuser:
            def __init__(self):
                self.connected = False

            @property
            def Connected(self):
                return self.connected

            @Connected.setter
            def Connected(self, value):
                if not value:
                    raise RuntimeError("Disconnect failed")
                self.connected = value

            Name = "ZWO Focuser"
            Description = "Test EAF"
            Position = 12345
            Temperature = 18.0

        focuser = FakeFocuser()
        client = types.ModuleType("win32com.client")
        client.Dispatch = mock.Mock(return_value=focuser)
        win32com = types.ModuleType("win32com")
        win32com.client = client

        with mock.patch.dict(
            sys.modules,
            {"win32com": win32com, "win32com.client": client},
        ):
            result = detect_focusers.probe_focuser("ASCOM.EAF.Focuser", 0)

        self.assertIn("error", result)
        self.assertIn("Could not disconnect focuser", result["error"])


if __name__ == "__main__":
    unittest.main()
