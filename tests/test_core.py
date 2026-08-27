import json
import random
import tempfile
import unittest
from pathlib import Path

from app.cs2 import build_placebo_cfg, cfg_paths_for_cs2, validate_paths, write_placebo_cfg
from app.results import (
    ResultSession,
    binomial_p_value,
    comparison_statistics,
    cumulative_duration_seconds,
    discover_result_sessions,
    fps_label,
    format_duration,
)
from app.test_engine import TestEngine
from app.settings import AppSettings, default_game_paths


class StatisticsTests(unittest.TestCase):
    def test_fps_labels_distinguish_uncapped_from_limits(self):
        self.assertEqual(fps_label(0), "Uncapped")
        self.assertEqual(fps_label(64), "fps_max 64")

    def test_exact_two_sided_binomial(self):
        self.assertEqual(binomial_p_value(5, 10), 1.0)
        self.assertAlmostEqual(binomial_p_value(10, 10), 0.001953125)
        self.assertEqual(binomial_p_value(0, 0), 1.0)

    def test_comparisons_are_independent(self):
        rows = [
            {"fps_a": 64, "fps_b": 0, "selected_a": True},
            {"fps_a": 64, "fps_b": 0, "selected_a": True},
            {"fps_a": 144, "fps_b": 0, "selected_a": False},
        ]
        stats = comparison_statistics(rows, [(64, 0), (144, 0)])
        self.assertEqual(stats[0]["trials"], 2)
        self.assertEqual(stats[0]["a_selected"], 2)
        self.assertEqual(stats[1]["trials"], 1)
        self.assertEqual(stats[1]["a_selected"], 0)


class EngineTests(unittest.TestCase):
    def test_mapping_and_initial_color_can_produce_every_combination(self):
        outcomes = set()
        for seed in range(100):
            engine = TestEngine(random.Random(seed))
            engine.configure_custom(60, 0, 1)
            engine.resume()
            trial = engine.next_trial()
            outcomes.add((trial.red_fps, trial.blue_fps, trial.initial_color))
        self.assertEqual(
            outcomes,
            {
                (60, 0, "RED"),
                (60, 0, "BLUE"),
                (0, 60, "RED"),
                (0, 60, "BLUE"),
            },
        )

    def test_uncapped_assignment_is_balanced_between_red_and_blue(self):
        engine = TestEngine(random.Random(7))
        engine.configure_preset({64: 10})
        engine.resume()
        uncapped_on_red = 0
        uncapped_on_blue = 0
        for _ in range(10):
            trial = engine.next_trial()
            uncapped_on_red += trial.red_fps == 0
            uncapped_on_blue += trial.blue_fps == 0
            engine.record_choice("RED")
        self.assertEqual(uncapped_on_red, 5)
        self.assertEqual(uncapped_on_blue, 5)

    def test_preset_plan_and_trial_record(self):
        engine = TestEngine(random.Random(4))
        engine.configure_preset({64: 2, 144: 1})
        engine.resume()
        trial = engine.next_trial()
        self.assertEqual({trial.red_fps, trial.blue_fps}, {0, 64})
        self.assertIn(trial.initial_color, ("RED", "BLUE"))
        row = engine.record_choice("RED")
        self.assertEqual(row["trial_number"], 1)
        self.assertEqual(row["test_mode"], "preset")
        self.assertIn("selected_a", row)
        self.assertIn("is_correct", row)

    def test_custom_allows_uncapped_on_either_side(self):
        engine = TestEngine(random.Random(2))
        engine.configure_custom(0, 600, 2)
        engine.resume()
        trial = engine.next_trial()
        self.assertEqual({trial.red_fps, trial.blue_fps}, {0, 600})
        correct_color = "RED" if trial.red_fps == 0 else "BLUE"
        self.assertTrue(engine.record_choice(correct_color)["is_correct"])
        second = engine.next_trial()
        wrong_color = "RED" if second.red_fps == 600 else "BLUE"
        self.assertFalse(engine.record_choice(wrong_color)["is_correct"])

    def test_pause_retries_unsubmitted_plan(self):
        engine = TestEngine(random.Random(1))
        engine.configure_custom(144, 165, 1)
        engine.resume()
        first = engine.next_trial()
        engine.pause()
        engine.resume()
        retried = engine.next_trial()
        self.assertEqual(retried.comparison_trial, 1)
        self.assertEqual((retried.red_fps, retried.blue_fps), (first.red_fps, first.blue_fps))
        self.assertEqual(len(engine.results), 0)


class ConfigTests(unittest.TestCase):
    def test_cfg_applies_selected_start_and_arms_opposite_toggle(self):
        cfg = build_placebo_cfg(240, 360, "BLUE")
        lines = cfg.splitlines()
        self.assertNotIn("fps_max 0", lines)
        self.assertIn("cl_showfps 0", cfg)
        self.assertIn("cl_hud_telemetry_frametime_show 0", cfg)
        self.assertIn("r_show_build_info false", cfg)
        self.assertIn('alias "placebo_toggle" "placebo_blue"', cfg)
        self.assertIn('alias "placebo_blue" "fps_max 360; say BLUE; alias placebo_toggle placebo_red"', cfg)
        self.assertEqual(lines[lines.index('alias "placebo_toggle" "placebo_blue"') + 1], "placebo_blue")
        self.assertEqual(lines.count("placebo_blue"), 1)
        self.assertIn('alias "reveal_truth" "say RED=240; say BLUE=360"', cfg)
        self.assertIn("game_type 1", cfg)
        self.assertIn("game_mode 2", cfg)
        self.assertIn("map de_dust2", cfg)

    def test_cfg_can_apply_red_first(self):
        lines = build_placebo_cfg(60, 0, "RED").splitlines()
        self.assertEqual(lines[lines.index('alias "placebo_toggle" "placebo_red"') + 1], "placebo_red")
        self.assertIn('alias "placebo_red" "fps_max 60; say RED; alias placebo_toggle placebo_blue"', lines)
        self.assertIn('alias "placebo_blue" "fps_max 0; say BLUE; alias placebo_toggle placebo_red"', lines)

    def test_trial_cfg_fully_replaces_setup_cfg(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg_path = Path(temp) / "placebo.cfg"
            write_placebo_cfg(cfg_path, 0, 0, "RED")
            write_placebo_cfg(cfg_path, 0, 60, "BLUE")
            self.assertEqual(cfg_path.read_text(encoding="utf-8"), build_placebo_cfg(0, 60, "BLUE"))
            self.assertEqual(cfg_path.read_text(encoding="utf-8").splitlines().count("placebo_blue"), 1)

    def test_cfg_rejects_unknown_initial_color(self):
        with self.assertRaisesRegex(ValueError, "Initial color"):
            build_placebo_cfg(60, 0, "GREEN")

    def test_session_saves_one_named_json_directly_in_results_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            session = ResultSession(temp, "My first test")
            row = {
                "timestamp": "2026-08-24T12:00:00",
                "test_mode": "custom",
                "trial_number": 1,
                "comparison_trial": 1,
                "fps_a": 240,
                "fps_b": 360,
                "red_fps": 240,
                "blue_fps": 360,
                "initial_color": "RED",
                "chosen_color": "BLUE",
                "chosen_fps": 360,
                "selected_a": False,
                "correct_fps": 360,
                "is_correct": True,
            }
            session.save(
                "custom",
                [row],
                [(240, 360)],
                started_at="2026-08-24T11:58:30",
                completed_at="2026-08-24T12:00:00",
                duration_seconds=90,
                status="completed",
            )
            self.assertTrue(session.json_path.is_file())
            self.assertEqual(session.json_path.parent, Path(temp))
            self.assertRegex(session.json_path.name, r"^\d{8}_\d{6}_My first test\.json$")
            self.assertEqual(list(Path(temp).glob("*.csv")), [])
            data = json.loads(session.json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["statistics"][0]["b_selected"], 1)
            self.assertEqual(data["statistics"][0]["correct"], 1)
            self.assertEqual(data["test_name"], "My first test")
            self.assertEqual(data["duration_seconds"], 90)
            self.assertEqual(data["status"], "completed")
            self.assertNotIn("session_directory", data)
            old_file = session.json_path
            session.rename("Renamed test")
            self.assertFalse(old_file.exists())
            self.assertEqual(session.json_path.parent, Path(temp))
            renamed = json.loads(session.json_path.read_text(encoding="utf-8"))
            self.assertEqual(renamed["test_name"], "Renamed test")
            self.assertRegex(session.json_path.name, r"^\d{8}_\d{6}_Renamed test\.json$")

    def test_default_paths_and_cfg_destination_stay_inside_cs2_cfg(self):
        defaults = default_game_paths()
        self.assertEqual(defaults["steam_executable"], r"C:\Program Files (x86)\Steam\steam.exe")
        self.assertTrue(defaults["placebo_cfg"].endswith(r"game\csgo\cfg\placebo.cfg"))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "game" / "bin" / "win64").mkdir(parents=True)
            (root / "game" / "bin" / "win64" / "cs2.exe").write_text("", encoding="utf-8")
            cfg, placebo = cfg_paths_for_cs2(root)
            cfg.mkdir(parents=True)
            steam = root / "steam.exe"
            steam.write_text("", encoding="utf-8")
            settings = AppSettings(
                cs2_path=str(root),
                cfg_directory=str(cfg),
                placebo_cfg=str(placebo),
                steam_executable=str(steam),
                results_directory=str(root / "Results"),
            )
            _status, errors = validate_paths(settings, create_results=True, test_write=True)
            self.assertEqual(errors, [])
            settings.placebo_cfg = str(root / "placebo.cfg")
            _status, errors = validate_paths(settings, test_write=True)
            self.assertIn("placebo.cfg must be inside the selected CS2 cfg folder.", errors)

    def test_discovers_and_loads_legacy_results_with_estimated_time(self):
        with tempfile.TemporaryDirectory() as temp:
            legacy_path = Path(temp) / "placebo_results.json"
            legacy_path.write_text(
                json.dumps(
                    {
                        "saved_at": "2026-08-24T17:04:36",
                        "mode": "adaptive",
                        "results": [
                            {
                                "timestamp": "2026-08-24T16:58:47",
                                "mode": "adaptive",
                                "fps_test": 64,
                                "sample": 1,
                                "red_fps": 64,
                                "blue_fps": 0,
                                "initial_colour": "BLUE",
                                "chosen_colour": "BLUE",
                                "chosen_fps": 0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            records = discover_result_sessions(temp)
            self.assertEqual(len(records), 1)
            session, data = records[0]
            self.assertEqual(session.test_name, "Legacy test")
            self.assertEqual(data["results"][0]["fps_a"], 64)
            self.assertTrue(data["results"][0]["is_correct"])
            self.assertTrue(data["duration_is_estimated"])
            self.assertEqual(data["duration_seconds"], 349)
            self.assertEqual(cumulative_duration_seconds(temp), 349)

    def test_duration_format_supports_cumulative_hours(self):
        self.assertEqual(format_duration(0), "00:00:00")
        self.assertEqual(format_duration(7384), "02:03:04")


if __name__ == "__main__":
    unittest.main()
