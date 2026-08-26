import json
import math
import re
from datetime import datetime
from pathlib import Path


def binomial_p_value(successes: int, total: int) -> float:
    if total <= 0:
        return 1.0
    observed = math.comb(total, successes) / (2**total)
    p_value = sum(
        math.comb(total, count) / (2**total)
        for count in range(total + 1)
        if math.comb(total, count) / (2**total) <= observed + 1e-15
    )
    return min(1.0, p_value)


def significance_text(p_value: float) -> str:
    if p_value < 0.001:
        return "Very significant"
    if p_value < 0.01:
        return "Highly significant"
    if p_value < 0.05:
        return "Significant"
    return "Not significant"


def comparison_statistics(
    rows: list[dict],
    comparisons: list[tuple[int, int]],
) -> list[dict]:
    statistics = []
    for fps_a, fps_b in comparisons:
        matching = [row for row in rows if row["fps_a"] == fps_a and row["fps_b"] == fps_b]
        total = len(matching)
        selected_a = sum(1 for row in matching if row["selected_a"])
        selected_b = total - selected_a
        p_value = binomial_p_value(selected_a, total)
        correct = sum(1 for row in matching if row.get("is_correct"))
        statistics.append(
            {
                "comparison": f"{fps_a} versus {fps_b}",
                "fps_a": fps_a,
                "fps_b": fps_b,
                "trials": total,
                "a_selected": selected_a,
                "b_selected": selected_b,
                "a_percentage": selected_a / total * 100 if total else 0.0,
                "b_percentage": selected_b / total * 100 if total else 0.0,
                "p_value": p_value,
                "significance": significance_text(p_value),
                "correct": correct,
                "incorrect": total - correct,
                "accuracy_percentage": correct / total * 100 if total else 0.0,
            }
        )
    return statistics


def format_duration(seconds: float | int | None) -> str:
    total = max(0, int(round(float(seconds or 0))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _higher_fps(fps_a: int, fps_b: int) -> int:
    if fps_a == 0 or fps_b == 0:
        return 0
    return max(fps_a, fps_b)


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _normalise_rows(data: dict) -> tuple[list[dict], list[tuple[int, int]]]:
    rows = []
    comparison_counts: dict[tuple[int, int], int] = {}
    default_mode = data.get("test_mode") or data.get("mode") or "legacy"
    for index, source in enumerate(data.get("results") or [], start=1):
        try:
            fps_a = int(source.get("fps_a", source.get("fps_test")))
            fps_b = int(source.get("fps_b", 0))
            red_fps = int(source.get("red_fps"))
            blue_fps = int(source.get("blue_fps"))
            chosen_fps = int(source.get("chosen_fps"))
        except (TypeError, ValueError):
            continue
        pair = (fps_a, fps_b)
        comparison_counts[pair] = comparison_counts.get(pair, 0) + 1
        correct_fps = _higher_fps(fps_a, fps_b)
        rows.append(
            {
                "timestamp": source.get("timestamp") or "",
                "test_mode": source.get("test_mode") or source.get("mode") or default_mode,
                "trial_number": int(source.get("trial_number", source.get("sample", index))),
                "comparison_trial": int(source.get("comparison_trial", comparison_counts[pair])),
                "fps_a": fps_a,
                "fps_b": fps_b,
                "red_fps": red_fps,
                "blue_fps": blue_fps,
                "initial_color": source.get("initial_color") or source.get("initial_colour") or "",
                "chosen_color": source.get("chosen_color") or source.get("chosen_colour") or "",
                "chosen_fps": chosen_fps,
                "selected_a": chosen_fps == fps_a,
                "correct_fps": int(source.get("correct_fps", correct_fps)),
                "is_correct": bool(source.get("is_correct", chosen_fps == correct_fps)),
            }
        )
    return rows, list(comparison_counts)


class ResultSession:
    """A JSON result stored directly inside the configured Results folder."""

    def __init__(self, results_directory: str | Path, test_name: str):
        root = Path(results_directory)
        root.mkdir(parents=True, exist_ok=True)
        self.stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.test_name = self.clean_name(test_name)
        self.directory = root
        self._json_path = self._unique_file(root, f"{self.stamp}_{self.test_name}")

    @classmethod
    def open_existing(cls, path_value: str | Path) -> "ResultSession":
        path = Path(path_value)
        if path.is_dir():
            generic = path / "results.json"
            candidates = [generic] if generic.is_file() else sorted(path.glob("*.json"))
            if not candidates:
                raise ValueError("Select a completed JSON result file.")
            path = candidates[0]
        if not path.is_file() or path.suffix.lower() != ".json":
            raise ValueError("Select a completed JSON result file.")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"{path.name} could not be read.") from error
        if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
            raise ValueError(f"{path.name} is not a supported test result file.")

        session = cls.__new__(cls)
        session.directory = path.parent
        file_match = re.match(r"^(\d{8}_\d{6})_(.+)$", path.stem)
        folder_match = re.match(r"^(\d{8}_\d{6})(?:_(.+))?$", path.parent.name)
        match = file_match or folder_match
        session.stamp = match.group(1) if match else datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback_name = file_match.group(2) if file_match else path.stem.replace("placebo_results", "Legacy test")
        if path.stem == "results":
            folder_name = folder_match.group(2) if folder_match and folder_match.lastindex == 2 else ""
            fallback_name = folder_name or (
                "Preset test" if (raw.get("test_mode") or raw.get("mode")) == "preset" else "Legacy test"
            )
        session.test_name = cls.clean_name(raw.get("test_name") or fallback_name)
        session._json_path = path
        return session

    @staticmethod
    def clean_name(value: str) -> str:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value).strip()
        name = re.sub(r"\s+", " ", name).rstrip(". ")
        return (name or "Untitled test")[:60]

    @staticmethod
    def _unique_file(directory: Path, stem: str, current: Path | None = None) -> Path:
        candidate = directory / f"{stem}.json"
        number = 2
        while candidate.exists() and candidate != current:
            candidate = directory / f"{stem}_{number}.json"
            number += 1
        return candidate

    @property
    def json_path(self) -> Path:
        return self._json_path

    def load(self) -> dict:
        try:
            raw = json.loads(self.json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"{self.json_path.name} could not be read.") from error
        rows, comparisons = _normalise_rows(raw)
        started_at = raw.get("started_at") or raw.get("session_started_at")
        completed_at = raw.get("completed_at") or raw.get("session_completed_at")
        duration = raw.get("duration_seconds", raw.get("session_duration_seconds"))
        duration_estimated = False
        if duration is None:
            first = _parse_datetime(started_at)
            if first is None and rows:
                first = _parse_datetime(rows[0].get("timestamp"))
            last = _parse_datetime(completed_at) or _parse_datetime(raw.get("saved_at"))
            duration = max(0.0, (last - first).total_seconds()) if first and last else 0.0
            duration_estimated = bool(first and last)
        status = raw.get("status") or ("completed" if completed_at else "saved")
        return {
            **raw,
            "test_name": self.test_name,
            "test_mode": raw.get("test_mode") or raw.get("mode") or "legacy",
            "results": rows,
            "comparisons": comparisons,
            "statistics": comparison_statistics(rows, comparisons),
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": float(duration or 0),
            "duration_is_estimated": duration_estimated,
            "status": status,
            "result_file": str(self.json_path),
        }

    def save(
        self,
        mode: str,
        rows: list[dict],
        comparisons: list[tuple[int, int]],
        *,
        started_at: str | None = None,
        completed_at: str | None = None,
        duration_seconds: float = 0.0,
        status: str = "in_progress",
    ) -> None:
        data = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "test_name": self.test_name,
            "test_mode": mode,
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": round(duration_seconds, 3),
            "result_file": str(self.json_path),
            "comparisons": comparisons,
            "statistics": comparison_statistics(rows, comparisons),
            "results": rows,
        }
        self.json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def rename(self, new_name: str) -> Path:
        old_json = self.json_path
        self.test_name = self.clean_name(new_name)
        new_json = self._unique_file(self.directory, f"{self.stamp}_{self.test_name}", current=old_json)
        if old_json.is_file() and old_json != new_json:
            old_json.rename(new_json)
        self._json_path = new_json
        self._update_saved_name()
        return self.directory

    def _update_saved_name(self) -> None:
        if not self.json_path.is_file():
            return
        data = json.loads(self.json_path.read_text(encoding="utf-8"))
        data["test_name"] = self.test_name
        data.pop("session_directory", None)
        data["result_file"] = str(self.json_path)
        self.json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def discover_result_sessions(results_directory: str | Path) -> list[tuple[ResultSession, dict]]:
    root = Path(results_directory)
    if not root.is_dir():
        return []
    records = []
    seen: set[Path] = set()
    for json_path in sorted(root.rglob("*.json"), reverse=True):
        try:
            resolved = json_path.resolve()
            if resolved in seen:
                continue
            session = ResultSession.open_existing(json_path)
            data = session.load()
        except (OSError, ValueError):
            continue
        seen.add(resolved)
        records.append((session, data))
    records.sort(
        key=lambda item: item[1].get("completed_at") or item[1].get("saved_at") or item[0].stamp,
        reverse=True,
    )
    return records


def cumulative_duration_seconds(results_directory: str | Path) -> float:
    return sum(data["duration_seconds"] for _session, data in discover_result_sessions(results_directory))
