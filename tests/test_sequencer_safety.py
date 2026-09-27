import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from argparse import Namespace
from contextlib import nullcontext
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

    def test_refresh_timeout_must_be_positive_and_finite(self):
        for value in ("0", "-1", "nan", "inf"):
            with (
                self.subTest(value=value),
                mock.patch.object(
                    sys,
                    "argv",
                    ["focus_sequencer.py", f"--refresh-timeout={value}"],
                ),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit):
                    focus_sequencer.parse_arguments()

    def test_temperature_override_must_be_finite(self):
        with (
            mock.patch.object(
                sys,
                "argv",
                ["focus_sequencer.py", "--temp=nan"],
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit):
                focus_sequencer.parse_arguments()

    def test_refresh_timeout_is_passed_to_subprocess(self):
        logger = mock.Mock()
        timeout = 12.5
        with (
            mock.patch.object(
                focus_sequencer.Path,
                "exists",
                return_value=True,
            ),
            mock.patch.object(focus_sequencer, "resolve_producer_python", return_value="python.exe"),
            mock.patch.object(
                focus_sequencer.subprocess,
                "run",
                side_effect=focus_sequencer.subprocess.TimeoutExpired("python.exe", timeout),
            ) as run,
        ):
            result = focus_sequencer.refresh_state_json(
                Path("state.json"),
                "main",
                logger,
                timeout,
            )

        self.assertIsNone(result)
        self.assertEqual(run.call_args.kwargs["timeout"], timeout)
        logger.error.assert_called_once()
        self.assertIn("refresh timeout", logger.error.call_args.args[0])

    def test_focuser_lock_rejects_a_second_run(self):
        logger = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                focus_sequencer,
                "SCRIPT_DIRECTORY",
                Path(directory),
            ):
                with focus_sequencer.focuser_execution_lock("ASCOM.Test.Focuser", logger):
                    with self.assertRaises(focus_sequencer.FocuserLockError):
                        with focus_sequencer.focuser_execution_lock(
                            "ASCOM.Test.Focuser",
                            logger,
                        ):
                            self.fail("Concurrent focuser lock unexpectedly succeeded")

    def test_move_is_aborted_if_focuser_becomes_busy(self):
        focuser = mock.Mock()
        focuser.IsMoving = True

        with self.assertRaisesRegex(RuntimeError, "became busy"):
            focus_sequencer.move_focuser(focuser, 12000, 5)

        focuser.Move.assert_not_called()

    def test_non_finite_focuser_temperature_is_rejected(self):
        focuser = mock.Mock()
        focuser.Temperature = float("nan")

        with self.assertRaisesRegex(RuntimeError, "non-finite temperature"):
            focus_sequencer.read_temperature(focuser)

    def test_move_timeout_is_enforced(self):
        class FakeFocuser:
            IsMoving = False
            halted = False

            def Move(self, target):
                self.target = target
                self.IsMoving = True

            def Halt(self):
                self.halted = True

        focuser = FakeFocuser()

        with (
            mock.patch.object(focus_sequencer.time, "monotonic", side_effect=[0, 2]),
            mock.patch.object(focus_sequencer.time, "sleep"),
            self.assertRaisesRegex(TimeoutError, "Halt command was sent"),
        ):
            focus_sequencer.move_focuser(focuser, 12000, 1)

        self.assertEqual(focuser.target, 12000)
        self.assertTrue(focuser.halted)

    def test_move_timeout_reports_if_halt_is_unavailable(self):
        class FakeFocuser:
            IsMoving = False

            def Move(self, target):
                self.IsMoving = True

        focuser = FakeFocuser()

        with (
            mock.patch.object(focus_sequencer.time, "monotonic", side_effect=[0, 2]),
            mock.patch.object(focus_sequencer.time, "sleep"),
            self.assertRaisesRegex(
                TimeoutError,
                "Halt command failed or is unsupported",
            ),
        ):
            focus_sequencer.move_focuser(focuser, 12000, 1)

    def test_target_calculation_rejects_non_finite_and_out_of_range_values(self):
        with self.assertRaisesRegex(ValueError, "non-finite target"):
            focus_sequencer.calculate_focus_target(
                10000,
                1e308,
                1e308,
                -1e308,
                0,
            )

        with self.assertRaisesRegex(ValueError, "outside the ASCOM position range"):
            focus_sequencer.calculate_focus_target(
                2_147_483_647,
                0,
                18.0,
                18.0,
                1,
            )

        with self.assertRaisesRegex(ValueError, "outside the ASCOM position range"):
            focus_sequencer.calculate_focus_target(
                0,
                0,
                18.0,
                18.0,
                -1,
            )

    def test_target_calculation_returns_thermal_and_filter_targets(self):
        self.assertEqual(
            focus_sequencer.calculate_focus_target(
                10000,
                -100,
                20.0,
                18.0,
                500,
            ),
            (2.0, 9800, 10300),
        )

    def run_simulated_cycle(
        self,
        state_path: Path,
        *,
        move_side_effect=None,
        save_side_effect=None,
        moving_after_timeout=False,
        temperature=18.0,
        state_overrides=None,
    ):
        args = Namespace(
            tube="guide",
            state_json=str(state_path),
            ascom_id=focus_sequencer.GUIDE_ASCOM_ID,
            backlash=0,
            min_correction=0,
            dry_run=False,
            temp=temperature,
            move_timeout=60.0,
            refresh_timeout=300.0,
            filter_position=None,
            config=None,
        )
        state = {
            "focus_ref": 10000,
            "temp_ref": 18.0,
            "model_tcf": -60.0,
            "last_temp_applied": 18.0,
            "last_focus_applied": 10000,
        }
        if state_overrides:
            state.update(state_overrides)
        active_filter = focus_sequencer.ActiveFilter(
            position=None,
            name="N/A",
            offset_steps=0,
            applies_to_main_tube=False,
        )
        logger = mock.Mock()
        focuser = mock.Mock()
        saved = []
        real_save_state = focus_sequencer.save_state

        def save_state_call(updated_state, path):
            if isinstance(save_side_effect, BaseException):
                raise save_side_effect
            if callable(save_side_effect):
                return save_side_effect(updated_state, path)
            real_save_state(updated_state, path)
            saved.append((updated_state.copy(), path))

        busy_states = [True, not moving_after_timeout]
        with (
            mock.patch.object(focus_sequencer, "parse_arguments", return_value=args),
            mock.patch.object(focus_sequencer, "setup_logging", return_value=logger),
            mock.patch.object(
                focus_sequencer,
                "focuser_execution_lock",
                return_value=nullcontext(),
            ),
            mock.patch.object(
                focus_sequencer,
                "resolve_state_json",
                return_value=state_path,
            ),
            mock.patch.object(
                focus_sequencer,
                "select_active_filter",
                return_value=(active_filter, None),
            ),
            mock.patch.object(focus_sequencer, "load_state", return_value=state.copy()),
            mock.patch.object(focus_sequencer, "connect_focuser", return_value=focuser),
            mock.patch.object(
                focus_sequencer,
                "check_not_busy",
                side_effect=busy_states,
            ),
            mock.patch.object(
                focus_sequencer,
                "refresh_state_json",
                return_value=state.copy(),
            ),
            mock.patch.object(focus_sequencer, "read_position", return_value=9000),
            mock.patch.object(focus_sequencer, "get_focuser_limits", return_value=(0, 20000)),
            mock.patch.object(
                focus_sequencer,
                "move_focuser_with_backlash",
                side_effect=move_side_effect or (lambda *args: 10000),
            ) as move,
            mock.patch.object(
                focus_sequencer,
                "save_state",
                side_effect=save_state_call,
            ) as save,
            mock.patch.object(focus_sequencer, "disconnect_focuser"),
        ):
            result = focus_sequencer.main()

        return result, logger, move, save, saved

    def test_complete_cycle_moves_and_persists_applied_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "sharpcap_focus_state_guide.json"

            result, logger, move, save, saved = self.run_simulated_cycle(state_path)

            self.assertEqual(result, 0)
            move.assert_called_once()
            save.assert_called_once()
            self.assertEqual(saved[0][0]["last_focus_applied"], 10000)
            self.assertEqual(saved[0][0]["last_temp_applied"], 18.0)
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8"))[
                    "last_focus_applied"
                ],
                10000,
            )
            logger.info.assert_any_call("END   | pos=10000 | reason=ok")

    def test_complete_cycle_timeout_does_not_persist_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "sharpcap_focus_state_guide.json"
            timeout = TimeoutError("Halt command was sent.")

            result, logger, move, save, saved = self.run_simulated_cycle(
                state_path,
                move_side_effect=timeout,
                moving_after_timeout=True,
            )

            self.assertEqual(result, 1)
            move.assert_called_once()
            save.assert_not_called()
            self.assertEqual(saved, [])
            logger.info.assert_any_call(
                "END   | pos=9000 (focuser still moving) | reason=move_timeout"
            )

    def test_complete_cycle_state_save_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "sharpcap_focus_state_guide.json"

            result, logger, move, save, saved = self.run_simulated_cycle(
                state_path,
                save_side_effect=OSError("Disk is full"),
            )

            self.assertEqual(result, 1)
            move.assert_called_once()
            save.assert_called_once()
            self.assertEqual(saved, [])
            logger.error.assert_any_call("Focus correction failed: Disk is full")
            logger.info.assert_any_call("END   | pos=9000 | reason=error")

    def test_complete_cycle_rejects_out_of_range_target_before_movement(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "sharpcap_focus_state_guide.json"

            result, logger, move, save, saved = self.run_simulated_cycle(
                state_path,
                temperature=18.5,
                state_overrides={"model_tcf": 1e308},
            )

            self.assertEqual(result, 1)
            move.assert_not_called()
            save.assert_not_called()
            self.assertEqual(saved, [])
            error_message = next(
                call.args[0]
                for call in logger.error.call_args_list
                if "Calculated focus target" in call.args[0]
            )
            self.assertIn("outside the ASCOM position range", error_message)

    def test_backlash_compensation_approaches_target_from_below(self):
        focuser = mock.Mock()

        with mock.patch.object(
            focus_sequencer,
            "move_focuser",
            side_effect=[500, 800],
        ) as move:
            result = focus_sequencer.move_focuser_with_backlash(
                focuser,
                target=800,
                current_position=1000,
                backlash_steps=300,
                timeout_s=10,
            )

        self.assertEqual(result, 800)
        self.assertEqual(
            [call.args[1] for call in move.call_args_list],
            [500, 800],
        )

    def test_target_is_clamped_to_available_focuser_limits(self):
        self.assertEqual(
            focus_sequencer.clamp_target_to_limits(12000, 0, 10000),
            (10000, True),
        )
        self.assertEqual(
            focus_sequencer.clamp_target_to_limits(-20, 0, 10000),
            (0, True),
        )
        self.assertEqual(
            focus_sequencer.clamp_target_to_limits(5000, 0, 10000),
            (5000, False),
        )

    def test_filter_reference_offset_must_remain_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "focus.properties"
            definitions = ["filter.default=1"]
            for position in range(1, 8):
                offset = 1 if position == 1 else 0
                definitions.extend(
                    [
                        f"filter.{position}.name=Filter {position}",
                        f"filter.{position}.offset_steps={offset}",
                    ]
                )
            config_path.write_text("\n".join(definitions), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "filter.1.offset_steps must be 0"):
                focus_sequencer.load_sequencer_config(config_path)

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
            refresh_timeout=300.0,
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
                "focuser_execution_lock",
                return_value=nullcontext(),
            ),
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
            mock.patch.object(
                focus_sequencer,
                "refresh_state_json",
                return_value=None,
            ) as refresh,
            mock.patch.object(focus_sequencer, "disconnect_focuser") as disconnect,
            mock.patch.object(focus_sequencer, "read_position") as read_position,
        ):
            result = focus_sequencer.main()

        self.assertEqual(result, 1)
        refresh.assert_called_once_with(
            Path("state.json"),
            "main",
            logger,
            300.0,
        )
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
