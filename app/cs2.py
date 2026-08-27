import os
import re
import subprocess
import time
from pathlib import Path

from app.settings import AppSettings


CS2_FOLDER = "Counter-Strike Global Offensive"
CS2_EXECUTABLE = Path("game/bin/win64/cs2.exe")


def cfg_paths_for_cs2(cs2_path: str | Path) -> tuple[Path, Path]:
    cfg = Path(cs2_path) / "game" / "csgo" / "cfg"
    return cfg, cfg / "placebo.cfg"


def _registry_steam_paths() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []

    locations = []
    keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Valve\Steam"),
    )
    for hive, key_name in keys:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                for value_name in ("SteamPath", "InstallPath"):
                    try:
                        locations.append(Path(winreg.QueryValueEx(key, value_name)[0]))
                        break
                    except OSError:
                        continue
        except OSError:
            continue
    return locations


def _steam_roots() -> list[Path]:
    candidates = _registry_steam_paths()
    for name in ("ProgramFiles(x86)", "ProgramFiles"):
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value) / "Steam")

    roots = []
    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen and candidate.exists():
            roots.append(candidate)
            seen.add(key)
    return roots


def _library_roots(steam_root: Path) -> list[Path]:
    libraries = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return libraries

    for value in re.findall(r'"path"\s+"([^"]+)"', text, flags=re.IGNORECASE):
        libraries.append(Path(value.replace("\\\\", "\\")))

    unique = []
    seen = set()
    for library in libraries:
        key = str(library).lower()
        if key not in seen:
            unique.append(library)
            seen.add(key)
    return unique


def valid_cs2_path(path: str | Path) -> bool:
    root = Path(path) if path else Path()
    return bool(path) and root.is_dir() and (root / CS2_EXECUTABLE).is_file()


def detect_paths() -> dict[str, str]:
    steam_roots = _steam_roots()
    detected: dict[str, str] = {}

    for root in steam_roots:
        steam_exe = root / "steam.exe"
        if steam_exe.is_file() and "steam_executable" not in detected:
            detected["steam_executable"] = str(steam_exe)

        for library in _library_roots(root):
            cs2_path = library / "steamapps" / "common" / CS2_FOLDER
            if valid_cs2_path(cs2_path):
                cfg, placebo_cfg = cfg_paths_for_cs2(cs2_path)
                detected["cs2_path"] = str(cs2_path)
                detected["cfg_directory"] = str(cfg)
                detected["placebo_cfg"] = str(placebo_cfg)
                return detected
    return detected


def apply_detected_paths(settings: AppSettings, replace: bool = False) -> bool:
    detected = detect_paths()
    changed = False
    for key, value in detected.items():
        current = getattr(settings, key)
        current_path = Path(current) if current else Path()
        if key == "placebo_cfg":
            current_valid = bool(current) and current_path.parent.is_dir()
        elif key == "cs2_path":
            current_valid = valid_cs2_path(current)
        elif key == "cfg_directory":
            current_valid = bool(current) and current_path.is_dir()
        else:
            current_valid = bool(current) and current_path.is_file()
        if replace or not current_valid:
            setattr(settings, key, value)
            changed = True
    return changed


def _writable_cfg(path: Path) -> bool:
    if path.name.lower() != "placebo.cfg" or not path.parent.is_dir():
        return False
    try:
        with path.open("a", encoding="utf-8"):
            pass
        return True
    except OSError:
        return False


def validate_paths(
    settings: AppSettings,
    create_results: bool = False,
    test_write: bool = False,
) -> tuple[dict[str, str], list[str]]:
    status: dict[str, str] = {}
    errors: list[str] = []

    if valid_cs2_path(settings.cs2_path):
        status["cs2_path"] = "CS2 found"
    else:
        status["cs2_path"] = "CS2 path required"
        errors.append("Select the CS2 installation folder.")

    cfg = Path(settings.cfg_directory) if settings.cfg_directory else Path()
    if settings.cfg_directory and cfg.is_dir():
        status["cfg_directory"] = "CFG folder found"
    else:
        status["cfg_directory"] = "CFG folder required"
        errors.append("Select the CS2 cfg folder.")

    target = Path(settings.placebo_cfg) if settings.placebo_cfg else Path()
    same_cfg = bool(settings.placebo_cfg) and os.path.normcase(str(target.parent.resolve())) == os.path.normcase(str(cfg.resolve()))
    target_ok = (
        bool(settings.placebo_cfg)
        and target.name.lower() == "placebo.cfg"
        and target.parent.is_dir()
        and same_cfg
    )
    if target_ok and (not test_write or _writable_cfg(target)):
        status["placebo_cfg"] = "placebo.cfg writable" if test_write else "placebo.cfg ready"
    else:
        status["placebo_cfg"] = "placebo.cfg destination required"
        errors.append("placebo.cfg must be inside the selected CS2 cfg folder.")

    steam = Path(settings.steam_executable) if settings.steam_executable else Path()
    if settings.steam_executable and steam.is_file():
        status["steam_executable"] = "Steam found"
    elif settings.steam_executable:
        status["steam_executable"] = "Steam executable not found"
        errors.append("The selected Steam executable does not exist.")
    else:
        status["steam_executable"] = "Steam path optional"

    results = Path(settings.results_directory) if settings.results_directory else Path()
    try:
        if create_results and settings.results_directory:
            results.mkdir(parents=True, exist_ok=True)
        results_ok = bool(settings.results_directory) and results.is_dir()
    except OSError:
        results_ok = False

    if results_ok:
        status["results_directory"] = "Results folder ready"
    else:
        status["results_directory"] = "Results folder required"
        errors.append("Choose a writable results folder.")

    return status, errors


def build_placebo_cfg(red_fps: int, blue_fps: int, initial_color: str) -> str:
    if initial_color not in ("RED", "BLUE"):
        raise ValueError("Initial color must be RED or BLUE.")
    first_alias = "placebo_red" if initial_color == "RED" else "placebo_blue"
    return f'''unbind "o"
unbind "l"

cl_showfps 0
cl_hud_telemetry_frametime_show 0
r_show_build_info false

alias "placebo_red" "fps_max {red_fps}; say RED; alias placebo_toggle placebo_blue"
alias "placebo_blue" "fps_max {blue_fps}; say BLUE; alias placebo_toggle placebo_red"
alias "placebo_toggle" "{first_alias}"
{first_alias}

bind "l" "placebo_toggle"

alias "reveal_truth" "say RED={red_fps}; say BLUE={blue_fps}"
bind "o" "reveal_truth"

game_type 1
game_mode 2
map de_dust2
'''


def write_placebo_cfg(path: str | Path, red_fps: int, blue_fps: int, initial_color: str) -> None:
    target = Path(path)
    if target.name.lower() != "placebo.cfg" or not target.parent.is_dir():
        raise OSError("placebo.cfg must be written inside an existing CS2 cfg folder.")
    target.write_text(build_placebo_cfg(red_fps, blue_fps, initial_color), encoding="utf-8")


def _hidden_kwargs() -> dict:
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def kill_cs2() -> None:
    if os.name != "nt":
        return
    subprocess.run(
        ["taskkill", "/F", "/T", "/IM", "cs2.exe"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        **_hidden_kwargs(),
    )
    for _ in range(100):
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq cs2.exe"],
            capture_output=True,
            text=True,
            check=False,
            **_hidden_kwargs(),
        )
        if "cs2.exe" not in result.stdout.lower():
            return
        time.sleep(0.1)
    raise RuntimeError("CS2 did not close within 10 seconds.")


def launch_cs2(steam_executable: str = "") -> None:
    if os.name != "nt":
        raise RuntimeError("Automatic CS2 launch is available on Windows only.")
    steam = Path(steam_executable) if steam_executable else None
    if steam and steam.is_file():
        subprocess.Popen(
            [str(steam), "-applaunch", "730"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_hidden_kwargs(),
        )
        return
    os.startfile("steam://rungameid/730")


def restart_cs2(settings: AppSettings, delay: float) -> None:
    kill_cs2()
    time.sleep(1.0)
    launch_cs2(settings.steam_executable)
    if delay > 0:
        time.sleep(delay)
