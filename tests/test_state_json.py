import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import focus_sequencer


class StateJsonTests(unittest.TestCase):
    def test_invalid_state_is_rejected_with_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "valid": False,
                        "status": "invalid",
                        "invalid_reason": "No current autofocus reference.",
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "marked invalid.*No current autofocus reference",
            ):
                focus_sequencer.load_state(state_path)

    def test_legacy_state_without_valid_flag_remains_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "focus_ref": 12345,
                        "temp_ref": 18.7,
                        "model_tcf": -977.18,
                        "last_temp_applied": 18.7,
                        "last_focus_applied": 12345,
                    }
                ),
                encoding="utf-8",
            )

            state = focus_sequencer.load_state(state_path)

        self.assertEqual(state["focus_ref"], 12345)

    def test_non_finite_state_values_are_rejected(self):
        valid_state = {
            "focus_ref": 12345,
            "temp_ref": 18.7,
            "model_tcf": -977.18,
            "last_temp_applied": 18.7,
            "last_focus_applied": 12345,
        }

        for key in (
            "focus_ref",
            "temp_ref",
            "model_tcf",
            "last_temp_applied",
            "last_focus_applied",
        ):
            for value in (float("nan"), float("inf"), float("-inf"), None, True):
                with (
                    self.subTest(key=key, value=value),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    state = valid_state.copy()
                    state[key] = value
                    state_path = Path(directory) / "state.json"
                    state_path.write_text(json.dumps(state), encoding="utf-8")

                    with self.assertRaisesRegex(ValueError, key):
                        focus_sequencer.load_state(state_path)

    def test_focus_reference_positions_must_be_non_negative_integers(self):
        valid_state = {
            "focus_ref": 12345,
            "temp_ref": 18.7,
            "model_tcf": -977.18,
            "last_temp_applied": 18.7,
            "last_focus_applied": 12345,
        }

        for key in ("focus_ref", "last_focus_applied"):
            for value in (-1, 12.5):
                with (
                    self.subTest(key=key, value=value),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    state = valid_state.copy()
                    state[key] = value
                    state_path = Path(directory) / "state.json"
                    state_path.write_text(json.dumps(state), encoding="utf-8")

                    with self.assertRaisesRegex(ValueError, f"{key} must be"):
                        focus_sequencer.load_state(state_path)

    def test_root_value_must_be_an_object(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must contain an object"):
                focus_sequencer.load_state(state_path)

    def test_save_state_replaces_json_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = {"last_focus_applied": 12345, "last_temp_applied": 18.7}

            focus_sequencer.save_state(state, state_path)

            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                state,
            )
            self.assertEqual(
                [path.name for path in Path(directory).iterdir()],
                ["state.json"],
            )

    def test_save_state_failure_preserves_original_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            original_content = '{"focus_ref": 12345}\n'
            state_path.write_text(original_content, encoding="utf-8")

            with self.assertRaises(TypeError):
                focus_sequencer.save_state({"invalid": object()}, state_path)

            self.assertEqual(state_path.read_text(encoding="utf-8"), original_content)
            self.assertEqual(
                [path.name for path in Path(directory).iterdir()],
                ["state.json"],
            )

    def test_replace_failure_preserves_original_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            original_content = '{"focus_ref": 12345}\n'
            state_path.write_text(original_content, encoding="utf-8")

            with (
                mock.patch.object(
                    focus_sequencer.os,
                    "replace",
                    side_effect=OSError("Replace failed"),
                ),
                self.assertRaisesRegex(OSError, "Replace failed"),
            ):
                focus_sequencer.save_state({"focus_ref": 20000}, state_path)

            self.assertEqual(state_path.read_text(encoding="utf-8"), original_content)
            self.assertEqual(
                [path.name for path in Path(directory).iterdir()],
                ["state.json"],
            )


if __name__ == "__main__":
    unittest.main()
