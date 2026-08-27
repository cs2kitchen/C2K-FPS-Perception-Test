import ctypes
import ctypes.wintypes
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from app.cs2 import (
    apply_detected_paths,
    cfg_paths_for_cs2,
    restart_cs2,
    validate_paths,
    write_placebo_cfg,
)
from app.results import (
    ResultSession,
    comparison_statistics,
    cumulative_duration_seconds,
    discover_result_sessions,
    fps_label,
    format_duration,
)
from app.settings import APP_NAME, PRESET_LEVELS, AppSettings, load_settings, resource_path
from app.test_engine import TestEngine


BG = "#0d1014"
SURFACE = "#151a20"
SURFACE_ALT = "#1b2129"
TEXT = "#edf1f5"
MUTED = "#8e99a6"
LINE = "#28313a"
ACCENT = "#d94a50"
ACCENT_DARK = "#402023"
ERROR = "#d77a72"


class ScrollableFrame(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, style="App.TFrame")
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.content = ttk.Frame(self.canvas, style="App.TFrame")
        self.window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._canvas_changed)
        self.canvas.bind("<Enter>", lambda _event: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda _event: self.canvas.unbind_all("<MouseWheel>"))

    def _content_changed(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _canvas_changed(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class GlobalHotkeys:
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    MOD_NOREPEAT = 0x4000
    KEYS = {1: (0x70, "F1"), 2: (0x71, "F2"), 4: (0x73, "F4")}

    def __init__(self, events: queue.Queue):
        self.events = events
        self.thread_id = None
        self.thread = None

    def start(self):
        if os.name != "nt":
            self.events.put(("hotkey_status", "Global hotkeys require Windows"))
            return
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self.thread_id = kernel32.GetCurrentThreadId()
        registered = []
        failed = []
        for hotkey_id, (virtual_key, label) in self.KEYS.items():
            if user32.RegisterHotKey(None, hotkey_id, self.MOD_NOREPEAT, virtual_key):
                registered.append(hotkey_id)
            else:
                failed.append(label)
        if failed:
            self.events.put(("hotkey_status", f"Unavailable global hotkeys: {', '.join(failed)}"))
        else:
            self.events.put(("hotkey_status", "Global hotkeys ready"))

        message = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == self.WM_HOTKEY and message.wParam in self.KEYS:
                self.events.put(self.KEYS[message.wParam][1])
        for hotkey_id in registered:
            user32.UnregisterHotKey(None, hotkey_id)

    def stop(self):
        if self.thread_id and os.name == "nt":
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)


class C2KApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("920x780")
        self.root.minsize(760, 600)
        self.root.configure(bg=BG)

        self.settings = load_settings()
        apply_detected_paths(self.settings)
        self.engine = TestEngine()
        self.session: ResultSession | None = None
        self.view_session: ResultSession | None = None
        self.view_data: dict | None = None
        self.last_statistics: list[dict] = []
        self.result_name_label = None
        self.result_window = None
        self.live_window = None
        self.choice_window = None
        self.closed = False
        self.restart_lock = threading.Lock()
        self.session_started_at: str | None = None
        self.session_completed_at: str | None = None
        self.session_elapsed_seconds = 0.0
        self.timer_started_monotonic: float | None = None
        self.timer_after_id = None
        self.cumulative_before_session = 0.0

        self.events: queue.Queue = queue.Queue()
        self.hotkeys = GlobalHotkeys(self.events)
        self.hotkey_status_var = tk.StringVar(value="Starting global hotkeys")
        self.live_timer_var = tk.StringVar(value="Session  00:00:00    Cumulative  00:00:00")
        self.history_time_var = tk.StringVar(value="Cumulative test time: 00:00:00")

        self.mode_var = tk.StringVar(value="preset")
        self.test_name_var = tk.StringVar()
        self.restart_delay_var = tk.StringVar(value=str(self.settings.restart_delay))
        self.preset_enabled = {fps: tk.BooleanVar(value=True) for fps in PRESET_LEVELS}
        self.preset_trials = {fps: tk.StringVar(value="10") for fps in PRESET_LEVELS}
        self.custom_a_var = tk.StringVar(value="240")
        self.custom_b_var = tk.StringVar(value="0")
        self.custom_trials_var = tk.StringVar(value="10")
        self.path_vars = {
            "cs2_path": tk.StringVar(),
            "cfg_directory": tk.StringVar(),
            "placebo_cfg": tk.StringVar(),
            "steam_executable": tk.StringVar(),
            "results_directory": tk.StringVar(),
        }
        self.path_status_labels: dict[str, ttk.Label] = {}

        self._setup_style()
        self._set_settings_vars()
        self._build_shell()
        self.hotkeys.start()
        self.root.after(75, self._process_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.show_page("setup" if not self.settings.first_run_complete else "test")

    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background=BG)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("Alt.TFrame", background=SURFACE_ALT)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Surface.TLabel", background=SURFACE, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("SurfaceMuted.TLabel", background=SURFACE, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 22))
        style.configure("PageTitle.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 18))
        style.configure("Section.TLabel", background=SURFACE, foreground=TEXT, font=("Segoe UI Semibold", 11))
        style.configure("BrandSub.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("TButton", background=SURFACE_ALT, foreground=TEXT, padding=(12, 7), borderwidth=0)
        style.map("TButton", background=[("active", "#252e37"), ("pressed", "#303b46")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#071014", font=("Segoe UI Semibold", 10), padding=(16, 9))
        style.map("Accent.TButton", background=[("active", "#ec6167"), ("pressed", "#b83b42")])
        style.configure("Nav.TButton", background=BG, foreground=MUTED, padding=(13, 8))
        style.map("Nav.TButton", foreground=[("active", TEXT)], background=[("active", SURFACE)])
        style.configure("TEntry", fieldbackground="#10151a", foreground=TEXT, insertcolor=TEXT, bordercolor=LINE, padding=6)
        style.configure("TSpinbox", fieldbackground="#10151a", foreground=TEXT, arrowcolor=MUTED, bordercolor=LINE, padding=5)
        style.configure("TRadiobutton", background=SURFACE, foreground=TEXT, indicatorcolor="#10151a")
        style.map("TRadiobutton", background=[("active", SURFACE)], indicatorcolor=[("selected", ACCENT)])
        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT, indicatorcolor="#10151a")
        style.map("TCheckbutton", background=[("active", SURFACE)], indicatorcolor=[("selected", ACCENT)])
        style.configure("Treeview", background="#10151a", foreground=TEXT, fieldbackground="#10151a", rowheight=28, borderwidth=0)
        style.configure("Treeview.Heading", background=SURFACE_ALT, foreground=TEXT, font=("Segoe UI Semibold", 9), padding=6)
        style.map("Treeview", background=[("selected", ACCENT_DARK)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=SURFACE_ALT, foreground=MUTED, padding=(14, 7))
        style.map("TNotebook.Tab", background=[("selected", SURFACE)], foreground=[("selected", TEXT)])

    def _build_shell(self):
        top = ttk.Frame(self.root, style="App.TFrame")
        top.pack(fill="x", padx=26, pady=(18, 12))

        brand = ttk.Frame(top, style="App.TFrame")
        brand.pack(side="left")
        logo_path = resource_path("data/logo.png")
        try:
            self.logo_original = tk.PhotoImage(file=str(logo_path))
            scale = max(1, max(self.logo_original.width(), self.logo_original.height()) // 48)
            self.logo_image = self.logo_original.subsample(scale, scale)
            self.root.iconphoto(True, self.logo_original)
            ttk.Label(brand, image=self.logo_image, style="TLabel").pack(side="left", padx=(0, 11))
        except tk.TclError:
            self.logo_original = None
            self.logo_image = None

        brand_text = ttk.Frame(brand, style="App.TFrame")
        brand_text.pack(side="left")
        ttk.Label(brand_text, text="C2K", style="Title.TLabel").pack(anchor="w")
        ttk.Label(brand_text, text="FPS Perception Test  •  CS2Kitchen", style="BrandSub.TLabel").pack(anchor="w")

        nav = ttk.Frame(top, style="App.TFrame")
        nav.pack(side="right")
        ttk.Button(nav, text="Test", style="Nav.TButton", command=lambda: self.show_page("test")).pack(side="left", padx=2)
        ttk.Button(nav, text="History", style="Nav.TButton", command=lambda: self.show_page("history")).pack(side="left", padx=2)
        ttk.Button(nav, text="Setup", style="Nav.TButton", command=lambda: self.show_page("setup")).pack(side="left", padx=2)

        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x")
        self.page_host = ttk.Frame(self.root, style="App.TFrame")
        self.page_host.pack(fill="both", expand=True)
        self.test_page = self._build_test_page()
        self.history_page = self._build_history_page()
        self.setup_page = self._build_setup_page()

    def _surface(self, parent, title: str, detail: str = "") -> ttk.Frame:
        frame = ttk.Frame(parent, style="Surface.TFrame", padding=(18, 15))
        ttk.Label(frame, text=title, style="Section.TLabel").pack(anchor="w")
        if detail:
            ttk.Label(frame, text=detail, style="SurfaceMuted.TLabel", wraplength=760, justify="left").pack(anchor="w", pady=(3, 12))
        else:
            ttk.Separator(frame).pack(fill="x", pady=(8, 12))
        return frame

    def _build_test_page(self) -> ttk.Frame:
        page = ScrollableFrame(self.page_host)
        body = page.content
        heading = ttk.Frame(body, style="App.TFrame")
        heading.pack(fill="x", padx=28, pady=(24, 14))
        ttk.Label(heading, text="Blind test", style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(heading, text="Configure the comparisons, then keep the conditions hidden until completion.", style="Muted.TLabel").pack(anchor="w", pady=(3, 0))

        mode = self._surface(body, "Mode")
        mode.pack(fill="x", padx=28, pady=(0, 12))
        name_row = ttk.Frame(mode, style="Surface.TFrame")
        name_row.pack(fill="x", pady=(0, 12))
        ttk.Label(name_row, text="Test name", style="Surface.TLabel").pack(side="left")
        ttk.Entry(name_row, textvariable=self.test_name_var, width=36).pack(side="right", fill="x", expand=True, padx=(24, 0))
        radio_row = ttk.Frame(mode, style="Surface.TFrame")
        radio_row.pack(fill="x")
        ttk.Radiobutton(radio_row, text="Preset", variable=self.mode_var, value="preset", command=self._refresh_mode).pack(side="left", padx=(0, 20))
        ttk.Radiobutton(radio_row, text="Custom", variable=self.mode_var, value="custom", command=self._refresh_mode).pack(side="left")

        self.mode_holder = ttk.Frame(body, style="App.TFrame")
        self.mode_holder.pack(fill="x", padx=28, pady=(0, 12))
        self.preset_frame = self._surface(
            self.mode_holder,
            "Preset comparisons",
            "Each enabled level is compared with Uncapped (fps_max 0) and analysed independently.",
        )
        header = ttk.Frame(self.preset_frame, style="Surface.TFrame")
        header.pack(fill="x", pady=(0, 4))
        ttk.Label(header, text="Enabled", style="SurfaceMuted.TLabel", width=10).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Comparison", style="SurfaceMuted.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="Trials", style="SurfaceMuted.TLabel").grid(row=0, column=2, sticky="e")
        header.columnconfigure(1, weight=1)

        for fps in PRESET_LEVELS:
            row = ttk.Frame(self.preset_frame, style="Surface.TFrame")
            row.pack(fill="x", pady=3)
            ttk.Checkbutton(row, variable=self.preset_enabled[fps], width=8).grid(row=0, column=0, sticky="w")
            ttk.Label(row, text=f"fps_max {fps} versus Uncapped", style="Surface.TLabel").grid(row=0, column=1, sticky="w")
            ttk.Spinbox(row, from_=1, to=10000, textvariable=self.preset_trials[fps], width=9).grid(row=0, column=2, sticky="e")
            row.columnconfigure(1, weight=1)

        self.custom_frame = self._surface(
            self.mode_holder,
            "Custom comparison",
            "FPS A and FPS B accept any integer from 0 upward. 0 selects Uncapped; other values use fps_max N.",
        )
        for label, variable in (
            ("FPS A", self.custom_a_var),
            ("FPS B", self.custom_b_var),
            ("Number of trials", self.custom_trials_var),
        ):
            row = ttk.Frame(self.custom_frame, style="Surface.TFrame")
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, style="Surface.TLabel").pack(side="left")
            ttk.Spinbox(row, from_=0 if label != "Number of trials" else 1, to=100000, textvariable=variable, width=12).pack(side="right")

        game = self._surface(body, "Game", "CS2 is restarted between trials so placebo.cfg is loaded cleanly.")
        game.pack(fill="x", padx=28, pady=(0, 12))
        delay_row = ttk.Frame(game, style="Surface.TFrame")
        delay_row.pack(fill="x")
        ttk.Label(delay_row, text="Delay after launching CS2", style="Surface.TLabel").pack(side="left")
        ttk.Spinbox(delay_row, from_=0, to=120, increment=0.5, textvariable=self.restart_delay_var, width=10).pack(side="right")

        controls = self._surface(body, "Controls")
        controls.pack(fill="x", padx=28, pady=(0, 12))
        ttk.Label(
            controls,
            text="F1   Start or Resume\nF2   Submit Trial\nF4   Pause and Save\nL     Toggle RED and BLUE\nO    Reveal mapping",
            style="Surface.TLabel",
            font=("Consolas", 10),
            justify="left",
        ).pack(anchor="w")
        ttk.Label(controls, textvariable=self.hotkey_status_var, style="SurfaceMuted.TLabel").pack(anchor="w", pady=(10, 0))

        actions = ttk.Frame(body, style="App.TFrame")
        actions.pack(fill="x", padx=28, pady=(0, 30))
        ttk.Button(actions, text="Start Blind Test", style="Accent.TButton", command=self.start_or_resume).pack(side="left")
        ttk.Button(actions, text="Open Results Folder", command=self.open_results_folder).pack(side="left", padx=8)
        self.last_results_button = ttk.Button(actions, text="View Last Results", command=self.show_last_results, state="disabled")
        self.last_results_button.pack(side="left")
        ttk.Button(actions, text="Browse Completed Tests", command=lambda: self.show_page("history")).pack(side="left", padx=(8, 0))

        self._refresh_mode()
        return page

    def _build_history_page(self) -> ttk.Frame:
        page = ttk.Frame(self.page_host, style="App.TFrame")
        heading = ttk.Frame(page, style="App.TFrame")
        heading.pack(fill="x", padx=28, pady=(24, 14))
        title = ttk.Frame(heading, style="App.TFrame")
        title.pack(side="left")
        ttk.Label(title, text="Completed tests", style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(title, textvariable=self.history_time_var, style="Muted.TLabel").pack(anchor="w", pady=(3, 0))
        ttk.Button(heading, text="Refresh", command=self.refresh_history).pack(side="right")

        table_host = ttk.Frame(page, style="App.TFrame")
        table_host.pack(fill="both", expand=True, padx=28, pady=(0, 12))
        columns = ("name", "saved", "status", "trials", "right", "wrong", "accuracy", "duration", "file")
        headings = {
            "name": "Test name",
            "saved": "Saved",
            "status": "Status",
            "trials": "Trials",
            "right": "Right",
            "wrong": "Wrong",
            "accuracy": "Accuracy",
            "duration": "Time",
            "file": "Result file",
        }
        widths = {
            "name": 180,
            "saved": 145,
            "status": 85,
            "trials": 55,
            "right": 55,
            "wrong": 55,
            "accuracy": 70,
            "duration": 80,
            "file": 190,
        }
        self.history_tree = ttk.Treeview(table_host, columns=columns, show="headings", selectmode="browse")
        for column in columns:
            self.history_tree.heading(column, text=headings[column])
            self.history_tree.column(column, width=widths[column], anchor="w" if column in ("name", "file") else "center")
        vertical = ttk.Scrollbar(table_host, orient="vertical", command=self.history_tree.yview)
        horizontal = ttk.Scrollbar(table_host, orient="horizontal", command=self.history_tree.xview)
        self.history_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.history_tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_host.columnconfigure(0, weight=1)
        table_host.rowconfigure(0, weight=1)
        self.history_tree.bind("<Double-1>", lambda _event: self.open_history_selection())
        self.history_records: dict[str, tuple[ResultSession, dict]] = {}

        actions = ttk.Frame(page, style="App.TFrame")
        actions.pack(fill="x", padx=28, pady=(0, 24))
        ttk.Button(actions, text="Open Selected", style="Accent.TButton", command=self.open_history_selection).pack(side="left")
        ttk.Button(actions, text="Rename Selected", command=self.rename_history_selection).pack(side="left", padx=8)
        ttk.Button(actions, text="Load Result File", command=self.load_result_file).pack(side="left")
        ttk.Button(actions, text="Open Results Folder", command=self.open_results_folder).pack(side="left", padx=8)
        return page

    def _build_setup_page(self) -> ttk.Frame:
        page = ScrollableFrame(self.page_host)
        body = page.content
        heading = ttk.Frame(body, style="App.TFrame")
        heading.pack(fill="x", padx=28, pady=(24, 14))
        ttk.Label(heading, text="First run setup", style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(heading, text="Confirm the game paths once. They are stored in your LocalAppData folder.", style="Muted.TLabel").pack(anchor="w", pady=(3, 0))

        paths = self._surface(
            body,
            "Paths",
            "Standard C: Steam locations are filled by default. Detection also checks every Steam library in libraryfolders.vdf.",
        )
        paths.pack(fill="x", padx=28, pady=(0, 12))
        labels = {
            "cs2_path": "CS2 installation",
            "cfg_directory": "CS2 cfg directory",
            "placebo_cfg": "placebo.cfg destination",
            "steam_executable": "Steam executable",
            "results_directory": "Results folder",
        }
        for key, label in labels.items():
            row = ttk.Frame(paths, style="Surface.TFrame")
            row.pack(fill="x", pady=5)
            ttk.Label(row, text=label, style="Surface.TLabel", width=24).grid(row=0, column=0, sticky="w")
            ttk.Entry(row, textvariable=self.path_vars[key]).grid(row=0, column=1, sticky="ew", padx=(0, 8))
            ttk.Button(row, text="Browse", command=lambda item=key: self._browse_path(item)).grid(row=0, column=2)
            status = ttk.Label(row, text="", style="SurfaceMuted.TLabel")
            status.grid(row=1, column=1, sticky="w", pady=(3, 0))
            self.path_status_labels[key] = status
            row.columnconfigure(1, weight=1)

        path_actions = ttk.Frame(paths, style="Surface.TFrame")
        path_actions.pack(fill="x", pady=(10, 0))
        ttk.Button(path_actions, text="Detect Again", command=self.detect_again).pack(side="left")
        ttk.Button(path_actions, text="Check Paths", command=self.refresh_path_status).pack(side="left", padx=8)

        launch = self._surface(
            body,
            "Required CS2 setup",
            "Saving Setup creates placebo.cfg in the selected CS2 cfg folder. The app does not inject, hook, overlay, or modify executables or DLLs.",
        )
        launch.pack(fill="x", padx=28, pady=(0, 12))
        ttk.Label(
            launch,
            text="Steam  >  Library  >  Counter Strike 2  >  Properties  >  General  >  Launch Options",
            style="Surface.TLabel",
            wraplength=760,
        ).pack(anchor="w")
        option_row = ttk.Frame(launch, style="Alt.TFrame", padding=(12, 9))
        option_row.pack(fill="x", pady=(10, 0))
        ttk.Label(option_row, text="+exec placebo", background=SURFACE_ALT, foreground=TEXT, font=("Consolas", 11)).pack(side="left")
        ttk.Button(option_row, text="Copy", command=self.copy_launch_option).pack(side="right")

        actions = ttk.Frame(body, style="App.TFrame")
        actions.pack(fill="x", padx=28, pady=(0, 30))
        ttk.Button(actions, text="Save Setup", style="Accent.TButton", command=self.save_setup).pack(side="left")
        self.setup_message = ttk.Label(actions, text="", style="Muted.TLabel")
        self.setup_message.pack(side="left", padx=12)
        return page

    def _refresh_mode(self):
        if not hasattr(self, "preset_frame"):
            return
        self.preset_frame.pack_forget()
        self.custom_frame.pack_forget()
        if self.mode_var.get() == "preset":
            self.preset_frame.pack(fill="x")
        else:
            self.custom_frame.pack(fill="x")

    def show_page(self, name: str):
        self.test_page.pack_forget()
        self.history_page.pack_forget()
        self.setup_page.pack_forget()
        if name == "setup":
            self._set_settings_vars()
            self.refresh_path_status()
            self.setup_page.pack(fill="both", expand=True)
        elif name == "history":
            self.refresh_history()
            self.history_page.pack(fill="both", expand=True)
        else:
            self.test_page.pack(fill="both", expand=True)

    def _set_settings_vars(self):
        for key, variable in self.path_vars.items():
            variable.set(getattr(self.settings, key))
        self.restart_delay_var.set(str(self.settings.restart_delay))

    def _settings_from_vars(self) -> AppSettings:
        for key, variable in self.path_vars.items():
            setattr(self.settings, key, variable.get().strip())
        return self.settings

    def _browse_path(self, key: str):
        current = self.path_vars[key].get()
        initial = str(Path(current).parent if current and key in ("placebo_cfg", "steam_executable") else current)
        if key == "steam_executable":
            selected = filedialog.askopenfilename(title="Select steam.exe", initialdir=initial or None, filetypes=[("Steam", "steam.exe"), ("Executable", "*.exe")])
        elif key == "placebo_cfg":
            selected = filedialog.asksaveasfilename(title="Select placebo.cfg destination", initialdir=initial or None, initialfile="placebo.cfg", defaultextension=".cfg", filetypes=[("CS2 config", "*.cfg")])
        else:
            selected = filedialog.askdirectory(title=f"Select {key.replace('_', ' ')}", initialdir=initial or None)
        if selected:
            self.path_vars[key].set(selected)
            if key == "cs2_path":
                cfg, placebo_cfg = cfg_paths_for_cs2(selected)
                self.path_vars["cfg_directory"].set(str(cfg))
                self.path_vars["placebo_cfg"].set(str(placebo_cfg))
            elif key == "cfg_directory":
                self.path_vars["placebo_cfg"].set(str(Path(selected) / "placebo.cfg"))
            self.refresh_path_status()

    def detect_again(self):
        self._settings_from_vars()
        if apply_detected_paths(self.settings, replace=True):
            self._set_settings_vars()
            self.setup_message.configure(text="Steam and CS2 paths detected", foreground=ACCENT)
        else:
            self.setup_message.configure(text="CS2 was not detected", foreground=ERROR)
        self.refresh_path_status()

    def refresh_path_status(self):
        self._settings_from_vars()
        statuses, _errors = validate_paths(self.settings)
        for key, label in self.path_status_labels.items():
            text = statuses.get(key, "")
            good = text.endswith(("found", "ready", "writable", "optional"))
            label.configure(text=text, foreground=ACCENT if good else ERROR)

    def copy_launch_option(self):
        self.root.clipboard_clear()
        self.root.clipboard_append("+exec placebo")
        self.setup_message.configure(text="Launch option copied", foreground=ACCENT)

    def save_setup(self):
        self._settings_from_vars()
        statuses, errors = validate_paths(self.settings, create_results=True, test_write=True)
        for key, label in self.path_status_labels.items():
            text = statuses.get(key, "")
            good = text.endswith(("found", "ready", "writable", "optional"))
            label.configure(text=text, foreground=ACCENT if good else ERROR)
        if errors:
            messagebox.showerror("Setup incomplete", "\n".join(dict.fromkeys(errors)), parent=self.root)
            return
        try:
            self.settings.restart_delay = float(self.restart_delay_var.get())
            write_placebo_cfg(self.settings.placebo_cfg, 0, 0, "RED")
            self.settings.first_run_complete = True
            self.settings.save()
        except (OSError, ValueError) as error:
            messagebox.showerror("Settings error", str(error), parent=self.root)
            return
        self.setup_message.configure(text="Setup saved", foreground=ACCENT)
        self.show_page("test")

    def _read_test_configuration(self):
        try:
            delay = float(self.restart_delay_var.get())
            if delay < 0:
                raise ValueError("Launch delay cannot be negative.")
        except ValueError as error:
            raise ValueError(str(error) if str(error) else "Enter a valid launch delay.") from error

        engine = TestEngine()
        if self.mode_var.get() == "preset":
            samples = {}
            for fps in PRESET_LEVELS:
                if self.preset_enabled[fps].get():
                    count = int(self.preset_trials[fps].get())
                    if count < 1:
                        raise ValueError(f"{fps} FPS needs at least 1 trial.")
                    samples[fps] = count
            engine.configure_preset(samples)
        else:
            engine.configure_custom(
                int(self.custom_a_var.get()),
                int(self.custom_b_var.get()),
                int(self.custom_trials_var.get()),
            )
        test_name = self.test_name_var.get().strip()
        if not test_name:
            if engine.mode == "preset":
                test_name = "Preset test"
            else:
                fps_a, fps_b = engine.comparisons[0]
                test_name = f"{fps_label(fps_a)} versus {fps_label(fps_b)}"
        return engine, delay, ResultSession.clean_name(test_name)

    def start_or_resume(self):
        if self.engine.running and not self.engine.paused:
            return

        if not self.engine.configured or self.engine.finished:
            statuses, errors = validate_paths(self.settings, create_results=True, test_write=True)
            if errors or not self.settings.first_run_complete:
                self.show_page("setup")
                messagebox.showerror("Setup required", "Complete Setup before starting a test.", parent=self.root)
                return
            try:
                engine, delay, test_name = self._read_test_configuration()
                session = ResultSession(self.settings.results_directory, test_name)
                self.settings.restart_delay = delay
                self.settings.save()
            except (ValueError, OSError) as error:
                messagebox.showerror("Cannot start test", str(error), parent=self.root)
                return
            self.engine = engine
            self.session = session
            self.session_started_at = datetime.now().isoformat(timespec="seconds")
            self.session_completed_at = None
            self.session_elapsed_seconds = 0.0
            self.timer_started_monotonic = None
            self.cumulative_before_session = cumulative_duration_seconds(self.settings.results_directory)
            self.test_name_var.set(session.test_name)
            self.last_statistics = []
            self.last_results_button.configure(state="disabled")
            if self.result_window and self.result_window.winfo_exists():
                self.result_window.destroy()
            self.result_window = None
            self.view_session = None
            self.view_data = None

        try:
            self.engine.resume()
            self._resume_timer()
        except RuntimeError as error:
            messagebox.showerror("Cannot resume test", str(error), parent=self.root)
            return

        self._show_live_controller()
        self.root.withdraw()
        self._start_trial()

    def _start_trial(self):
        if not self.engine.running or self.engine.paused:
            return
        trial = self.engine.next_trial()
        if trial is None:
            self._finish_test()
            return
        try:
            write_placebo_cfg(
                self.settings.placebo_cfg,
                trial.red_fps,
                trial.blue_fps,
                trial.initial_color,
            )
        except OSError as error:
            self._pause_for_error(f"Could not write placebo.cfg.\n\n{error}")
            return
        threading.Thread(target=self._restart_game, daemon=True).start()

    def _resume_timer(self):
        if self.timer_started_monotonic is None:
            self.timer_started_monotonic = time.monotonic()
        self._update_timer_label()

    def _pause_timer(self):
        if self.timer_started_monotonic is not None:
            self.session_elapsed_seconds += time.monotonic() - self.timer_started_monotonic
            self.timer_started_monotonic = None
        self._update_timer_label()

    def _current_elapsed_seconds(self) -> float:
        elapsed = self.session_elapsed_seconds
        if self.timer_started_monotonic is not None:
            elapsed += time.monotonic() - self.timer_started_monotonic
        return elapsed

    def _update_timer_label(self):
        session_time = self._current_elapsed_seconds()
        cumulative = self.cumulative_before_session + session_time
        self.live_timer_var.set(
            f"Session  {format_duration(session_time)}    Cumulative  {format_duration(cumulative)}"
        )
        if not self.closed and self.timer_after_id is None:
            self.timer_after_id = self.root.after(500, self._timer_tick)

    def _timer_tick(self):
        self.timer_after_id = None
        self._update_timer_label()

    def _restart_game(self):
        try:
            with self.restart_lock:
                restart_cs2(self.settings, self.settings.restart_delay)
        except Exception as error:
            if not self.closed:
                self.root.after(0, lambda: self._pause_for_error(f"Could not restart CS2.\n\n{error}"))

    def _pause_for_error(self, text: str):
        if self.engine.configured:
            self.engine.pause()
            self._pause_timer()
        self._close_choice_window()
        self._close_live_window()
        self.root.deiconify()
        self.root.lift()
        messagebox.showerror("Test paused", text, parent=self.root)

    def submit_trial(self):
        if not self.engine.trial_active or self.choice_window:
            return
        self._show_choice_window()

    def _show_live_controller(self):
        if self.live_window and self.live_window.winfo_exists():
            self.live_window.lift()
            return
        window = tk.Toplevel()
        self.live_window = window
        window.title("Blind Test Controls")
        window.geometry("410x390")
        window.resizable(False, False)
        window.configure(bg=BG)
        window.attributes("-topmost", True)
        window.protocol("WM_DELETE_WINDOW", lambda: None)

        tk.Label(window, text="Blind Test Running", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 19)).pack(pady=(25, 5))
        tk.Label(window, textvariable=self.live_timer_var, bg=BG, fg=MUTED, font=("Consolas", 10)).pack(pady=(0, 18))
        tk.Button(window, text="Submit Trial", command=self.submit_trial, bg=ACCENT, fg="#100708", activebackground="#ec6167", relief="flat", font=("Segoe UI Semibold", 11), height=2).pack(fill="x", padx=30, pady=(0, 9))
        tk.Button(window, text="Pause and Save", command=self.pause_and_save, bg=SURFACE_ALT, fg=TEXT, activebackground="#252e37", activeforeground=TEXT, relief="flat", font=("Segoe UI", 10), height=2).pack(fill="x", padx=30)

        controls = tk.Frame(window, bg=SURFACE, highlightbackground=LINE, highlightthickness=1)
        controls.pack(fill="x", padx=30, pady=20)
        tk.Label(controls, text="Controls", bg=SURFACE, fg=TEXT, font=("Segoe UI Semibold", 10)).pack(anchor="w", padx=13, pady=(11, 6))
        tk.Label(
            controls,
            text="F1  Start or Resume\nF2  Submit Trial\nF4  Pause and Save\nL    Toggle RED and BLUE\nO   Reveal mapping",
            bg=SURFACE,
            fg=MUTED,
            justify="left",
            font=("Consolas", 9),
        ).pack(anchor="w", padx=13, pady=(0, 11))

    def _show_choice_window(self):
        window = tk.Toplevel()
        self.choice_window = window
        window.title("Submit Trial")
        window.geometry("430x245")
        window.resizable(False, False)
        window.configure(bg=BG)
        window.attributes("-topmost", True)
        window.protocol("WM_DELETE_WINDOW", lambda: None)
        tk.Label(window, text="Which color felt faster?", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 17)).pack(pady=(28, 6))
        tk.Label(window, text="Choose from what you felt.", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack()
        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", padx=28, pady=27)
        tk.Button(buttons, text="RED", command=lambda: self.record_choice("RED"), bg="#272c33", fg=TEXT, activebackground="#343b44", activeforeground=TEXT, relief="flat", font=("Segoe UI Semibold", 12), height=2).pack(side="left", fill="x", expand=True, padx=(0, 7))
        tk.Button(buttons, text="BLUE", command=lambda: self.record_choice("BLUE"), bg="#272c33", fg=TEXT, activebackground="#343b44", activeforeground=TEXT, relief="flat", font=("Segoe UI Semibold", 12), height=2).pack(side="left", fill="x", expand=True, padx=(7, 0))
        window.bind("<r>", lambda _event: self.record_choice("RED"))
        window.bind("<b>", lambda _event: self.record_choice("BLUE"))
        window.focus_force()

    def record_choice(self, color: str):
        try:
            self.engine.record_choice(color)
            self._save_results()
        except (RuntimeError, OSError) as error:
            self._pause_for_error(f"The trial could not be saved.\n\n{error}")
            return
        self._close_choice_window()
        if self.engine.finished:
            self._finish_test()
        else:
            self.root.after(250, self._start_trial)

    def _save_results(self):
        if self.session:
            if self.session_completed_at:
                status = "completed"
            elif self.engine.paused:
                status = "paused"
            else:
                status = "in_progress"
            self.session.save(
                self.engine.mode,
                self.engine.results,
                self.engine.comparisons,
                started_at=self.session_started_at,
                completed_at=self.session_completed_at,
                duration_seconds=self._current_elapsed_seconds(),
                status=status,
            )

    def pause_and_save(self):
        if not self.engine.configured or self.engine.finished:
            return
        self.engine.pause()
        self._pause_timer()
        try:
            self._save_results()
        except OSError as error:
            messagebox.showerror("Save error", str(error), parent=self.root)
        self._close_choice_window()
        self._close_live_window()
        self.root.deiconify()
        self.root.lift()
        location = str(self.session.directory) if self.session else self.settings.results_directory
        messagebox.showinfo("Test paused", f"Results saved.\n\n{location}\n\nPress F1 to resume.", parent=self.root)

    def _finish_test(self):
        self.engine.complete()
        self._pause_timer()
        self.session_completed_at = datetime.now().isoformat(timespec="seconds")
        try:
            self._save_results()
        except OSError as error:
            messagebox.showerror("Save error", str(error), parent=self.root)
        self._close_choice_window()
        self._close_live_window()
        self.root.deiconify()
        self.root.lift()
        self.last_statistics = comparison_statistics(self.engine.results, self.engine.comparisons)
        self.last_results_button.configure(state="normal")
        self.show_last_results()

    def show_last_results(self):
        if not self.session or not self.session.json_path.is_file():
            return
        try:
            data = self.session.load()
        except (OSError, ValueError) as error:
            messagebox.showerror("Cannot load results", str(error), parent=self.root)
            return
        self._show_result_data(self.session, data)

    def _show_result_data(self, session: ResultSession, data: dict):
        if self.result_window and self.result_window.winfo_exists():
            self.result_window.destroy()
        self.view_session = session
        self.view_data = data
        self.last_statistics = data["statistics"]
        window = tk.Toplevel(self.root)
        self.result_window = window
        test_name = session.test_name
        window.title(f"{test_name} | {APP_NAME}")
        window.geometry("1080x760")
        window.minsize(780, 600)
        window.configure(bg=BG)

        header = ttk.Frame(window, style="App.TFrame")
        header.pack(fill="x", padx=24, pady=(20, 12))
        self.result_name_label = ttk.Label(header, text=test_name, style="PageTitle.TLabel")
        self.result_name_label.pack(side="left")
        ttk.Button(header, text="Open Results Folder", command=self.open_session_folder).pack(side="right")
        ttk.Button(header, text="Rename Test", command=self.rename_current_test).pack(side="right", padx=8)

        rows = data["results"]
        correct = sum(1 for row in rows if row.get("is_correct"))
        wrong = len(rows) - correct
        accuracy = correct / len(rows) * 100 if rows else 0.0
        tally = ttk.Frame(window, style="Surface.TFrame", padding=(16, 11))
        tally.pack(fill="x", padx=24, pady=(0, 12))
        tally_numbers = ttk.Frame(tally, style="Surface.TFrame")
        tally_numbers.pack(fill="x")
        ttk.Label(tally_numbers, text=f"RIGHT  {correct}", style="Section.TLabel").pack(side="left")
        ttk.Label(tally_numbers, text=f"WRONG  {wrong}", style="Section.TLabel").pack(side="left", padx=28)
        ttk.Label(tally_numbers, text=f"ACCURACY  {accuracy:.1f}%", style="Section.TLabel").pack(side="left")
        ttk.Label(tally_numbers, text="Correct means the higher actual FPS. Uncapped means fps_max 0.", style="SurfaceMuted.TLabel").pack(side="right")
        estimate = " (estimated from older file)" if data.get("duration_is_estimated") else ""
        cumulative = cumulative_duration_seconds(self.settings.results_directory)
        ttk.Label(
            tally,
            text=(
                f"SESSION TIME  {format_duration(data.get('duration_seconds'))}{estimate}    "
                f"CUMULATIVE TIME  {format_duration(cumulative)}    "
                f"FILE  {session.json_path.name}"
            ),
            style="SurfaceMuted.TLabel",
        ).pack(anchor="w", pady=(8, 0))

        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=24, pady=(0, 10))
        summary_tab = ttk.Frame(notebook, style="App.TFrame")
        trials_tab = ttk.Frame(notebook, style="App.TFrame")
        notebook.add(summary_tab, text="Summary")
        notebook.add(trials_tab, text="Trials")

        chart_host = ttk.Frame(summary_tab, style="Surface.TFrame", padding=(12, 8))
        chart_host.pack(fill="both", expand=True, pady=(10, 10))
        self._draw_results_chart(chart_host)

        self._build_comparison_table(summary_tab)
        self._build_trial_table(trials_tab)
        ttk.Label(window, text="Exact two sided binomial test against 50 percent guessing. Each comparison is analysed independently.", style="Muted.TLabel").pack(anchor="w", padx=24, pady=(0, 16))

    def _build_comparison_table(self, parent):
        table_host = ttk.Frame(parent, style="App.TFrame")
        table_host.pack(fill="x")
        columns = (
            "comparison",
            "trials",
            "a",
            "b",
            "a_rate",
            "b_rate",
            "right",
            "wrong",
            "accuracy",
            "p",
            "significance",
        )
        tree = ttk.Treeview(table_host, columns=columns, show="headings", height=min(6, len(self.last_statistics)))
        headings = {
            "comparison": "Comparison",
            "trials": "Trials",
            "a": "A selected",
            "b": "B selected",
            "a_rate": "A percent",
            "b_rate": "B percent",
            "right": "Right",
            "wrong": "Wrong",
            "accuracy": "Accuracy",
            "p": "p value",
            "significance": "Significance",
        }
        widths = {
            "comparison": 205,
            "trials": 60,
            "a": 80,
            "b": 80,
            "a_rate": 80,
            "b_rate": 80,
            "right": 60,
            "wrong": 60,
            "accuracy": 75,
            "p": 85,
            "significance": 125,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor="center", stretch=True)
        for stat in self.last_statistics:
            tree.insert(
                "",
                "end",
                values=(
                    stat["comparison"],
                    stat["trials"],
                    stat["a_selected"],
                    stat["b_selected"],
                    f'{stat["a_percentage"]:.1f}%',
                    f'{stat["b_percentage"]:.1f}%',
                    stat["correct"],
                    stat["incorrect"],
                    f'{stat["accuracy_percentage"]:.1f}%',
                    f'{stat["p_value"]:.6f}',
                    stat["significance"],
                ),
            )
        scrollbar = ttk.Scrollbar(table_host, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=scrollbar.set)
        tree.pack(fill="x")
        scrollbar.pack(fill="x")

    def _build_trial_table(self, parent):
        table_host = ttk.Frame(parent, style="App.TFrame")
        table_host.pack(fill="both", expand=True, pady=10)
        columns = (
            "trial",
            "comparison",
            "initial",
            "red",
            "blue",
            "chosen_color",
            "chosen_fps",
            "correct_fps",
            "result",
        )
        headings = {
            "trial": "Sample",
            "comparison": "Comparison",
            "initial": "Initial color",
            "red": "RED setting",
            "blue": "BLUE setting",
            "chosen_color": "Chosen color",
            "chosen_fps": "Chosen setting",
            "correct_fps": "Correct setting",
            "result": "Result",
        }
        tree = ttk.Treeview(table_host, columns=columns, show="headings")
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=125 if column != "comparison" else 205, anchor="center", stretch=True)
        tree.tag_configure("right", foreground=TEXT)
        tree.tag_configure("wrong", foreground=ERROR)
        for row in (self.view_data or {}).get("results", []):
            result = "RIGHT" if row.get("is_correct") else "WRONG"
            tree.insert(
                "",
                "end",
                values=(
                    row["trial_number"],
                    f'{fps_label(row["fps_a"])} versus {fps_label(row["fps_b"])}',
                    row["initial_color"],
                    fps_label(row["red_fps"]),
                    fps_label(row["blue_fps"]),
                    row["chosen_color"],
                    fps_label(row["chosen_fps"]),
                    fps_label(row["correct_fps"]),
                    result,
                ),
                tags=(result.lower(),),
            )
        vertical = ttk.Scrollbar(table_host, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(table_host, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_host.columnconfigure(0, weight=1)
        table_host.rowconfigure(0, weight=1)

    def _draw_results_chart(self, host):
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError:
            ttk.Label(host, text="The graph is unavailable. Exact results are shown below.", style="Surface.TLabel").pack(pady=30)
            return

        labels = [stat["comparison"] for stat in self.last_statistics]
        values = [stat["a_percentage"] for stat in self.last_statistics]
        colors = [ACCENT if stat["p_value"] < 0.05 else "#697480" for stat in self.last_statistics]
        figure = Figure(figsize=(8.8, 3.7), dpi=100, facecolor=SURFACE)
        axis = figure.add_subplot(111, facecolor=SURFACE)
        bars = axis.bar(range(len(values)), values, color=colors, width=0.58)
        axis.axhline(50, color="#aeb7c0", linewidth=1, linestyle=(0, (4, 4)), label="50 percent chance")
        axis.set_ylim(0, 108)
        axis.set_ylabel("FPS A selected (%)", color=MUTED)
        axis.set_xticks(range(len(labels)), labels)
        axis.tick_params(axis="x", colors=TEXT, labelsize=9)
        axis.tick_params(axis="y", colors=MUTED, labelsize=8)
        axis.grid(axis="y", color=LINE, linewidth=0.6)
        axis.set_axisbelow(True)
        for side in axis.spines.values():
            side.set_color(LINE)
        legend = axis.legend(loc="upper right", frameon=False, fontsize=8)
        for item in legend.get_texts():
            item.set_color(MUTED)
        for bar, stat in zip(bars, self.last_statistics):
            marker = " *" if stat["p_value"] < 0.05 else ""
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                min(104, bar.get_height() + 3),
                f'{stat["a_percentage"]:.1f}%\np {stat["p_value"]:.4g}{marker}',
                ha="center",
                va="bottom",
                color=TEXT,
                fontsize=8,
            )
        figure.tight_layout(pad=1.4)
        canvas = FigureCanvasTkAgg(figure, master=host)
        canvas.draw()
        canvas.get_tk_widget().configure(bg=SURFACE, highlightthickness=0)
        canvas.get_tk_widget().pack(fill="both", expand=True)

    def refresh_history(self):
        if not hasattr(self, "history_tree"):
            return
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        self.history_records.clear()
        records = discover_result_sessions(self.settings.results_directory)
        total_time = sum(data["duration_seconds"] for _session, data in records)
        self.history_time_var.set(
            f"{len(records)} saved test{'s' if len(records) != 1 else ''}    Cumulative test time: {format_duration(total_time)}"
        )
        for index, (session, data) in enumerate(records):
            rows = data["results"]
            correct = sum(1 for row in rows if row.get("is_correct"))
            wrong = len(rows) - correct
            accuracy = correct / len(rows) * 100 if rows else 0.0
            saved = data.get("completed_at") or data.get("saved_at") or session.stamp
            saved_text = str(saved).replace("T", " ")[:19]
            duration = format_duration(data.get("duration_seconds"))
            if data.get("duration_is_estimated"):
                duration = f"~{duration}"
            item = f"result_{index}"
            self.history_records[item] = (session, data)
            self.history_tree.insert(
                "",
                "end",
                iid=item,
                values=(
                    session.test_name,
                    saved_text,
                    str(data.get("status", "saved")).replace("_", " ").title(),
                    len(rows),
                    correct,
                    wrong,
                    f"{accuracy:.1f}%",
                    duration,
                    session.json_path.name,
                ),
            )

    def _selected_history_record(self) -> tuple[ResultSession, dict] | None:
        selection = self.history_tree.selection()
        if not selection:
            messagebox.showinfo("Select a test", "Select a saved test first.", parent=self.root)
            return None
        return self.history_records.get(selection[0])

    def open_history_selection(self):
        record = self._selected_history_record()
        if record:
            self._show_result_data(*record)

    def load_result_file(self):
        selected = filedialog.askopenfilename(
            title="Load an older test result",
            initialdir=self.settings.results_directory or None,
            filetypes=[("C2K result files", "*.json"), ("All files", "*.*")],
        )
        if not selected:
            return
        try:
            session = ResultSession.open_existing(selected)
            data = session.load()
        except (OSError, ValueError) as error:
            messagebox.showerror("Cannot load results", str(error), parent=self.root)
            return
        self._show_result_data(session, data)

    def rename_history_selection(self):
        record = self._selected_history_record()
        if not record:
            return
        session, _data = record
        if self._rename_session(session, self.root):
            self.refresh_history()

    def _rename_session(self, session: ResultSession, parent) -> bool:
        new_name = simpledialog.askstring(
            "Rename test",
            "Test name",
            initialvalue=session.test_name,
            parent=parent,
        )
        if not new_name:
            return False
        try:
            is_active = bool(
                self.session and session.json_path.resolve() == self.session.json_path.resolve()
            )
            is_viewed = bool(
                self.view_session and session.json_path.resolve() == self.view_session.json_path.resolve()
            )
            session.rename(new_name)
            if is_active:
                self.session = session
                self.test_name_var.set(session.test_name)
                self._save_results()
            if is_viewed:
                self.view_session = session
        except (OSError, ValueError) as error:
            messagebox.showerror("Rename failed", str(error), parent=parent)
            return False
        return True

    def rename_current_test(self):
        session = self.view_session
        if not session:
            return
        if not self._rename_session(session, self.result_window or self.root):
            return
        try:
            data = session.load()
            self._show_result_data(session, data)
        except (OSError, ValueError) as error:
            messagebox.showerror("Cannot reload results", str(error), parent=self.root)
        self.refresh_history()

    def open_results_folder(self):
        self._open_folder(Path(self.settings.results_directory))

    def open_session_folder(self):
        session = self.view_session or self.session
        if session:
            self._open_folder(session.directory)

    def _open_folder(self, path: Path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(path)
            else:
                raise OSError("Opening folders is available on Windows only.")
        except OSError as error:
            messagebox.showerror("Cannot open folder", str(error), parent=self.root)

    def _process_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event == "F1":
                    self.start_or_resume()
                elif event == "F2":
                    self.submit_trial()
                elif event == "F4":
                    self.pause_and_save()
                elif isinstance(event, tuple) and event[0] == "hotkey_status":
                    self.hotkey_status_var.set(event[1])
        except queue.Empty:
            pass
        if not self.closed:
            self.root.after(75, self._process_events)

    def _close_choice_window(self):
        if self.choice_window:
            try:
                self.choice_window.destroy()
            except tk.TclError:
                pass
            self.choice_window = None

    def _close_live_window(self):
        if self.live_window:
            try:
                self.live_window.destroy()
            except tk.TclError:
                pass
            self.live_window = None

    def close(self):
        self.closed = True
        if self.engine.configured and self.session:
            try:
                self._pause_timer()
                self._save_results()
            except OSError:
                pass
        self.hotkeys.stop()
        self._close_choice_window()
        self._close_live_window()
        self.root.destroy()
