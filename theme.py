import traceback
import tkinter as tk
from tkinter import ttk

from logger import log

BG       = "#121212"
SURFACE  = "#1e1e1e"
ELEVATED = "#282828"
GREEN    = "#1DB954"
TEXT     = "#ffffff"
TEXT_DIM = "#b3b3b3"
SELECT   = "#333333"


def apply_theme(root: tk.Tk) -> None:
    root.configure(bg=BG)
    s = ttk.Style(root)
    s.theme_use("clam")

    s.configure(".",
                background=BG, foreground=TEXT,
                fieldbackground=SURFACE, bordercolor=ELEVATED,
                darkcolor=BG, lightcolor=BG,
                troughcolor=ELEVATED,
                selectbackground=GREEN, selectforeground=TEXT,
                font=(None, 10))

    s.configure("TFrame", background=BG)
    s.configure("TLabel", background=BG, foreground=TEXT)

    s.configure("TButton",
                background=ELEVATED, foreground=TEXT,
                bordercolor=ELEVATED, focuscolor=GREEN,
                padding=(8, 4))
    s.map("TButton",
          background=[("active", SELECT), ("pressed", SELECT)],
          foreground=[("disabled", TEXT_DIM)])

    s.configure("TNotebook", background=BG, bordercolor=BG, tabmargins=0)
    s.configure("TNotebook.Tab",
                background=ELEVATED, foreground=TEXT_DIM,
                padding=(12, 6))
    s.map("TNotebook.Tab",
          background=[("selected", BG), ("active", SELECT)],
          foreground=[("selected", TEXT), ("active", TEXT)])

    s.configure("Treeview",
                background=SURFACE, foreground=TEXT,
                fieldbackground=SURFACE, rowheight=24,
                bordercolor=BG)
    s.configure("Treeview.Heading",
                background=ELEVATED, foreground=TEXT_DIM,
                bordercolor=BG, relief="flat")
    s.map("Treeview",
          background=[("selected", GREEN)],
          foreground=[("selected", TEXT)])
    s.map("Treeview.Heading",
          background=[("active", SELECT)])

    s.configure("TScrollbar",
                background=ELEVATED, troughcolor=SURFACE,
                bordercolor=BG, arrowcolor=TEXT_DIM, relief="flat")
    s.map("TScrollbar",
          background=[("active", SELECT)])

    s.configure("TProgressbar",
                background=GREEN, troughcolor=ELEVATED,
                bordercolor=BG)

    s.configure("TCheckbutton", background=BG, foreground=TEXT,
                focuscolor=GREEN)
    s.map("TCheckbutton", background=[("active", BG)])

    s.configure("TLabelframe", background=BG, bordercolor=ELEVATED)
    s.configure("TLabelframe.Label", background=BG, foreground=TEXT_DIM)

    s.configure("TCombobox",
                fieldbackground=SURFACE, background=ELEVATED,
                foreground=TEXT, selectbackground=SELECT,
                selectforeground=TEXT, bordercolor=ELEVATED,
                arrowcolor=TEXT)
    s.map("TCombobox",
          fieldbackground=[("readonly", SURFACE), ("disabled", BG)],
          selectbackground=[("readonly", SURFACE)],
          selectforeground=[("readonly", TEXT)],
          foreground=[("readonly", TEXT), ("disabled", TEXT_DIM)],
          background=[("active", SELECT), ("readonly", ELEVATED)])

    # Style the dropdown listbox (plain tk widget, not ttk)
    root.option_add("*TCombobox*Listbox.background", SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", GREEN)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)


def make_icon() -> "tk.PhotoImage | None":
    try:
        from PIL import Image, ImageDraw, ImageTk  # type: ignore
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([0, 0, size - 1, size - 1], fill=GREEN)
        m = int(size * 0.27)
        pts = [
            (m, int(m * 0.7)),
            (m, size - int(m * 0.7)),
            (size - int(m * 0.45), size // 2),
        ]
        draw.polygon(pts, fill="white")
        return ImageTk.PhotoImage(img)
    except Exception:
        log.debug("Could not create app icon:\n%s", traceback.format_exc())
        return None
