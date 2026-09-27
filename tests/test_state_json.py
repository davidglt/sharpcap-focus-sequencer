import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
