"""Init v2's one text dialog (the User 2026-10-04): the glass has no text field of its own, so the name on Lila's card
and a pack's Connect endpoint are typed here. Dark, small, centred; Enter = OK, Escape = cancel; one at a time."""
from __future__ import annotations

_OPEN: dict = {"top": None}
GROUND, GROUND2, INK, INK2, EMBER, EMBER_DEEP = "#10131A", "#151922", "#E9E5DB", "#8B93A6", "#D9A05B", "#7A5223"


KEEP_ABOVE_MS = 400


def keep_above(top, root=None) -> None:
    """X (the User 2026-10-10, .101: one stray click sent the Language box behind the Wall's black plate): the Wall is a
    desktop-layer window, so a box over it re-asserts itself while it is open - topmost again and lifted every
    KEEP_ABOVE_MS, and at once when the Wall itself is clicked. Never a transient of the Wall (a transient follows its
    parent's layer)."""
    import tkinter as tk

    def tick():
        if _OPEN.get("top") is not top:
            return
        try:
            top.attributes("-topmost", True)
            top.lift()
            top.after(KEEP_ABOVE_MS, tick)
        except tk.TclError:
            pass
    top._keep_above = tick                       # for tests
    try:
        top.after(KEEP_ABOVE_MS, tick)
    except tk.TclError:
        return
    if root is not None:
        try:
            root.bind("<Button>", lambda _e: (top.lift() if _OPEN.get("top") is top else None), add="+")
        except tk.TclError:
            pass


def ask(root, title: str, fields, on_ok, *, on_cancel=None, presets=None, preset=""):
    """fields: [(key, label, initial, secret)]. Calls on_ok({key: text}) after OK. Returns the Toplevel (tests drive
    it) or the one already open.

    presets: [(id, label, {key: value})] - THE KIND, as on the browser's Connect page (the User 2026-10-10, .101: the
    desk Wall's Connect dialog had Address / Key / Model and no Local, Claude API or xAI): a row of choices above the
    fields; picking one fills its fields, and its id is handed back as {"preset": id}."""
    import tkinter as tk

    if _OPEN["top"] is not None:
        try:
            _OPEN["top"].lift()
            return _OPEN["top"]
        except tk.TclError:
            _OPEN["top"] = None
    top = tk.Toplevel(root)
    _OPEN["top"] = top
    top.title(title)
    top.configure(bg=GROUND)
    try:
        top.attributes("-type", "dialog")          # X11: a dialog the window manager centres and focuses
    except tk.TclError:
        pass
    top.attributes("-topmost", True)
    ents = {}
    kind = tk.StringVar(top, value=str(preset or ""))
    row0 = 0
    if presets:
        tk.Label(top, text="Kind", fg=INK2, bg=GROUND, font=("DejaVu Sans Mono", 11)).grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        bar0 = tk.Frame(top, bg=GROUND)
        bar0.grid(row=0, column=1, sticky="w", padx=(0, 14), pady=(12, 4))

        def pick(values):
            for k_, v_ in (values or {}).items():
                if k_ in ents:
                    ents[k_].delete(0, "end")
                    ents[k_].insert(0, str(v_ or ""))
        for pid, plabel, values in presets:
            tk.Radiobutton(bar0, text=plabel, variable=kind, value=pid, command=lambda v=values: pick(v),
                           fg=INK, bg=GROUND, selectcolor=GROUND2, activebackground=EMBER_DEEP, activeforeground=INK,
                           highlightthickness=0, font=("DejaVu Sans Mono", 11), indicatoron=False, relief="flat",
                           padx=10, pady=3).pack(side="left", padx=(0, 6))
        row0 = 1
    for i, (key, label, initial, secret) in enumerate(fields):
        pad = (12 if i == 0 and not presets else 4, 4)
        tk.Label(top, text=label, fg=INK2, bg=GROUND, font=("DejaVu Sans Mono", 11)).grid(
            row=row0 + i, column=0, sticky="w", padx=14, pady=pad)
        e = tk.Entry(top, width=48, fg=INK, bg=GROUND2, insertbackground=EMBER, relief="flat",
                     font=("DejaVu Sans", 13), show="•" if secret else "")
        e.insert(0, initial or "")
        e.grid(row=row0 + i, column=1, padx=(0, 14), pady=pad)
        ents[key] = e

    def close(*_a):
        _OPEN["top"] = None
        try:
            top.destroy()
        except tk.TclError:
            pass

    def cancel(*_a):
        close()
        if on_cancel:
            on_cancel()

    def ok(*_a):
        vals = {k: e.get().strip() for k, e in ents.items()}
        if presets and kind.get():
            vals["preset"] = kind.get()
        close()
        on_ok(vals)

    bar = tk.Frame(top, bg=GROUND)
    bar.grid(row=row0 + len(fields), column=0, columnspan=2, sticky="e", padx=14, pady=12)
    for label, fn in (("Cancel", cancel), ("OK", ok)):
        tk.Button(bar, text=label, command=fn, fg=INK, bg=GROUND2, activebackground=EMBER_DEEP, relief="flat",
                  font=("DejaVu Sans Mono", 11), padx=14).pack(side="left", padx=4)
    top.bind("<Return>", ok)
    top.bind("<Escape>", cancel)
    top.protocol("WM_DELETE_WINDOW", cancel)
    top.update_idletasks()
    sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
    top.geometry(f"+{(sw - top.winfo_reqwidth()) // 2}+{(sh - top.winfo_reqheight()) // 3}")
    top.lift()
    try:
        top.focus_force()
    except tk.TclError:
        pass
    if ents:
        next(iter(ents.values())).focus_set()
    keep_above(top, root)
    top._init2 = {"ok": ok, "cancel": cancel, "entries": ents, "kind": kind}     # for tests
    return top
