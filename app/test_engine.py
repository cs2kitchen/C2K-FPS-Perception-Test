import random
from dataclasses import dataclass
from datetime import datetime

from app.settings import PRESET_LEVELS


@dataclass(frozen=True)
class TrialPlan:
    fps_a: int
    fps_b: int
    comparison_trial: int
    a_is_red: bool


@dataclass(frozen=True)
class Trial:
    trial_number: int
    comparison_trial: int
    fps_a: int
    fps_b: int
    red_fps: int
    blue_fps: int
    initial_color: str


class TestEngine:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.mode = "preset"
        self.plans: list[TrialPlan] = []
        self.comparisons: list[tuple[int, int]] = []
        self.results: list[dict] = []
        self.index = 0
        self.current: Trial | None = None
        self.configured = False
        self.running = False
        self.paused = False

    @property
    def trial_active(self) -> bool:
        return self.current is not None and self.running and not self.paused

    @property
    def finished(self) -> bool:
        return self.configured and self.index >= len(self.plans)

    def configure_preset(self, samples: dict[int, int]) -> None:
        plans = []
        comparisons = []
        for fps in PRESET_LEVELS:
            count = samples.get(fps, 0)
            if count < 1:
                continue
            comparisons.append((fps, 0))
            plans.extend(
                TrialPlan(fps, 0, number, a_is_red)
                for number, a_is_red in enumerate(self._balanced_assignments(count), start=1)
            )
        if not plans:
            raise ValueError("Enable at least one FPS comparison.")
        self._configure("preset", plans, comparisons)

    def configure_custom(self, fps_a: int, fps_b: int, trials: int) -> None:
        if fps_a < 0 or fps_b < 0:
            raise ValueError("FPS values cannot be negative.")
        if fps_a == fps_b:
            raise ValueError("FPS A and FPS B must be different.")
        if trials < 1:
            raise ValueError("Number of trials must be at least 1.")
        plans = [
            TrialPlan(fps_a, fps_b, number, a_is_red)
            for number, a_is_red in enumerate(self._balanced_assignments(trials), start=1)
        ]
        self._configure("custom", plans, [(fps_a, fps_b)])

    def _balanced_assignments(self, count: int) -> list[bool]:
        assignments = [True] * (count // 2) + [False] * (count // 2)
        if count % 2:
            assignments.append(self.rng.choice((True, False)))
        self.rng.shuffle(assignments)
        return assignments

    def _configure(
        self,
        mode: str,
        plans: list[TrialPlan],
        comparisons: list[tuple[int, int]],
    ) -> None:
        self.mode = mode
        self.plans = plans
        self.comparisons = comparisons
        self.results = []
        self.index = 0
        self.current = None
        self.configured = True
        self.running = False
        self.paused = False

    def resume(self) -> None:
        if not self.configured or self.finished:
            raise RuntimeError("No test is ready to run.")
        self.running = True
        self.paused = False

    def next_trial(self) -> Trial | None:
        if not self.running or self.paused:
            return None
        if self.index >= len(self.plans):
            self.current = None
            return None

        plan = self.plans[self.index]
        if plan.a_is_red:
            red_fps, blue_fps = plan.fps_a, plan.fps_b
        else:
            red_fps, blue_fps = plan.fps_b, plan.fps_a
        initial_color = self.rng.choice(("RED", "BLUE"))
        self.current = Trial(
            trial_number=len(self.results) + 1,
            comparison_trial=plan.comparison_trial,
            fps_a=plan.fps_a,
            fps_b=plan.fps_b,
            red_fps=red_fps,
            blue_fps=blue_fps,
            initial_color=initial_color,
        )
        return self.current

    def record_choice(self, color: str) -> dict:
        if not self.trial_active or color not in ("RED", "BLUE"):
            raise RuntimeError("There is no active trial to submit.")
        trial = self.current
        chosen_fps = trial.red_fps if color == "RED" else trial.blue_fps
        correct_fps = self._higher_fps(trial.fps_a, trial.fps_b)
        result = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "test_mode": self.mode,
            "trial_number": trial.trial_number,
            "comparison_trial": trial.comparison_trial,
            "fps_a": trial.fps_a,
            "fps_b": trial.fps_b,
            "red_fps": trial.red_fps,
            "blue_fps": trial.blue_fps,
            "initial_color": trial.initial_color,
            "chosen_color": color,
            "chosen_fps": chosen_fps,
            "selected_a": chosen_fps == trial.fps_a,
            "correct_fps": correct_fps,
            "is_correct": chosen_fps == correct_fps,
        }
        self.results.append(result)
        self.index += 1
        self.current = None
        return result

    @staticmethod
    def _higher_fps(fps_a: int, fps_b: int) -> int:
        if fps_a == 0:
            return fps_a
        if fps_b == 0:
            return fps_b
        return max(fps_a, fps_b)

    def pause(self) -> None:
        self.running = False
        self.paused = True
        self.current = None

    def complete(self) -> None:
        self.running = False
        self.paused = False
        self.current = None
