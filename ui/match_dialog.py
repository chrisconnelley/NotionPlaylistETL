import tkinter as tk
from tkinter import ttk

from notion import SKIP
from theme import BG, SURFACE, GREEN, TEXT, TEXT_DIM


class NotionMatchDialog(tk.Toplevel):
    """
    Shown when a record can't be matched automatically in Notion.
    Presents candidates for the user to select, or lets them create a new record.

    Parameters
    ----------
    item_type : str
        Human-readable label for what is being matched, e.g. "Artist" or "Song".
    item_name : str
        The name that failed to match.
    candidates : list[dict]
        Each dict has {"id": notion_page_id, "name": display_name}.

    result : str | None
        Set after the dialog closes.
        - Notion page ID if the user picked a candidate.
        - None if the user chose "Create New".
    """

    def __init__(self, parent, item_type: str, item_name: str, candidates: list[dict]):
        super().__init__(parent)
        self.title(f"Match {item_type}")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.grab_set()

        self.result = None
        self._candidates = candidates
        self._item_type = item_type
        self._item_name = item_name

        self._build_ui()

        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")
        self.protocol("WM_DELETE_WINDOW", self._create_new)

    def _build_ui(self):
        # Title with item type
        ttk.Label(self, text=f"Match {self._item_type}",
                  font=(None, 11, "bold")).pack(padx=16, pady=(14, 2), anchor="w")

        # From Spotify section
        ttk.Label(self, text="From Spotify:",
                  font=(None, 9), foreground=TEXT_DIM).pack(padx=16, pady=(4, 2), anchor="w")
        ttk.Label(self, text=f'"{self._item_name}"',
                  font=(None, 10)).pack(padx=32, pady=(0, 8), anchor="w")

        # Options section
        ttk.Label(self, text="Existing options in Notion:",
                  font=(None, 9), foreground=TEXT_DIM).pack(padx=16, pady=(0, 4), anchor="w")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=(0, 8))

        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=16)

        self._listbox = tk.Listbox(
            list_frame, width=44, height=8,
            bg=SURFACE, fg=TEXT,
            selectbackground=GREEN, selectforeground=TEXT,
            highlightbackground=BG, highlightcolor=GREEN,
            borderwidth=0, font=(None, 10),
        )
        self._listbox.pack(side="left", fill="both", expand=True)
        self._listbox.bind("<Double-Button-1>", self._match_selected)

        sb = ttk.Scrollbar(list_frame, orient="vertical",
                           command=self._listbox.yview)
        sb.pack(side="right", fill="y")
        self._listbox.configure(yscrollcommand=sb.set)

        if self._candidates:
            for c in self._candidates:
                display = c["name"]
                if c.get("spotify_url"):
                    display += f"  […{c['spotify_url'][-4:]}]"
                self._listbox.insert("end", display)
            self._listbox.selection_set(0)  # pre-select first for easy confirmation
            self._listbox.see(0)
        else:
            self._listbox.insert("end", f"(no similar {self._item_type.lower()}s found in Notion)")
            self._listbox.configure(state="disabled")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=(10, 0))

        btn_bar = ttk.Frame(self)
        btn_bar.pack(fill="x", padx=16, pady=(8, 14))

        ttk.Button(btn_bar, text="Create New",
                   command=self._create_new).pack(side="right")
        self._match_btn = ttk.Button(btn_bar, text="Match Selected",
                                     command=self._match_selected,
                                     state="normal" if self._candidates else "disabled")
        self._match_btn.pack(side="right", padx=(0, 6))
        ttk.Button(btn_bar, text="Skip",
                   command=self._skip).pack(side="left")

    def _match_selected(self, _event=None):
        sel = self._listbox.curselection()
        if not sel:
            return
        self.result = self._candidates[sel[0]]["id"]
        self.destroy()

    def _create_new(self):
        self.result = None
        self.destroy()

    def _skip(self):
        self.result = SKIP
        self.destroy()
