import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path


APP_NAME = "C2K FPS Perception Test"
APP_VERSION = "1.0.0"
PRESET_LEVELS = (64, 144, 240, 360, 400, 600)
DEFAULT_STEAM_ROOT = Path(r"C:\Program Files (x86)\Steam")


def default_game_paths() -> dict[str, str]:
    cs2 = DEFAULT_STEAM_ROOT / "steamapps" / "common" / "Counter-Strike Global Offensive"
    cfg = cs2 / "game" / "csgo" / "cfg"
    return {
        "cs2_path": str(cs2),
        "cfg_directory": str(cfg),
        "placebo_cfg": str(cfg / "placebo.cfg"),
        "steam_executable": str(DEFAULT_STEAM_ROOT / "steam.exe"),
    }


def config_directory() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / APP_NAME


def settings_path() -> Path:
    return config_directory() / "settings.json"


def default_results_directory() -> Path:
    profile = Path(os.environ.get("USERPROFILE", Path.home()))
    documents = profile / "Documents"
    return documents / APP_NAME / "Results"


def resource_path(relative: str | Path) -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    root = Path(bundle) if bundle else Path(__file__).resolve().parents[1]
    return root / Path(relative)


@dataclass
class AppSettings:
    cs2_path: str = ""
    cfg_directory: str = ""
    placebo_cfg: str = ""
    steam_executable: str = ""
    results_directory: str = ""
    restart_delay: float = 5.0
    first_run_complete: bool = False

    def __post_init__(self) -> None:
        defaults = default_game_paths()
        for key, value in defaults.items():
            if not getattr(self, key):
                setattr(self, key, value)
        if not self.results_directory:
            self.results_directory = str(default_results_directory())

    @classmethod
    def from_dict(cls, values: dict) -> "AppSettings":
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in values.items() if key in allowed})

    def save(self, path: Path | None = None) -> None:
        target = path or settings_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def load_settings(path: Path | None = None) -> AppSettings:
    target = path or settings_path()
    if not target.exists():
        return AppSettings()
    try:
        values = json.loads(target.read_text(encoding="utf-8"))
        return AppSettings.from_dict(values)
    except (OSError, ValueError, TypeError):
        return AppSettings()
