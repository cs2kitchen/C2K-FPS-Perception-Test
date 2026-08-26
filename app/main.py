import ctypes
import os
import tkinter as tk

from app.ui import C2KApp


def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def main() -> None:
    enable_dpi_awareness()
    root = tk.Tk()
    C2KApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
