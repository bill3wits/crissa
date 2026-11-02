"""Borderless floating picture: slow ease from a corner tile to center.

Move by dragging the face. Resize from an 8px edge. Esc / double-click
eases back. JPEG only — no Brave, no CDP.
"""
from __future__ import annotations

import time
from typing import Callable

from PIL import Image, ImageFilter, ImageTk

import house_wall_prefs as prefs

FT_SRC = (240, 0, 267, 400)
BAR_H = 36
GROUND = "#10131A"
INK = "#E9E5DB"
INK3 = "#4A5163"
EMBER = "#D9A05B"
EMBER2 = "#F0C489"
FONT = ("DejaVu Sans Mono", 8)


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def lerp_box(a: tuple, b: tuple, t: float) -> tuple[int, int, int, int]:
    k = ease(t)
    return tuple(int(round(a[i] + (b[i] - a[i]) * k)) for i in range(4))


def dest_box(sw: int, sh: int, prefs: dict, bar_h: int = 0) -> tuple[int, int, int, int]:
    """Centered tile. FT is 4:6 at 570px tall @ 1.00; ROOM is 16:9.
    bar_h is the control strip under the picture; the pair is what is centred."""
    size = float(prefs.get("ft_size") or 1.0)
    if prefs.get("ft_mode") == "room":
        w = int(min(sw * 0.62, 1280) * size)
        h = int(w * 9 / 16)
    else:
        h = int(570 * size)
        w = int(h * 4 / 6)
    w, h = max(160, w), max(120, h)
    return ((sw - w) // 2, (sh - h - bar_h) // 2, w, h)


def crop_frame(im: Image.Image, prefs: dict, wh: tuple[int, int]) -> Image.Image:
    mode = prefs.get("ft_mode") or "ft"
    if mode == "room":
        # whole clip, 16:9 cover
        iw, ih = im.size
        ta = 16 / 9
        sa = iw / max(1, ih)
        if sa > ta:
            nw = int(ih * ta)
            x0 = (iw - nw) // 2
            tile = im.crop((x0, 0, x0 + nw, ih))
        else:
            nh = int(iw / ta)
            y0 = (ih - nh) // 2
            tile = im.crop((0, y0, iw, y0 + nh))
    else:
        x, y, w, h = FT_SRC
        tile = im.crop((x, y, min(im.width, x + w), min(im.height, y + h)))
    out = tile.resize(wh, Image.Resampling.BILINEAR).convert("RGB")
    if prefs.get("blur"):
        rad = 9 if max(wh) >= 480 else 7
        out = out.filter(ImageFilter.GaussianBlur(rad))
    return out


class Float:
    def __init__(self, root, origin: tuple, get_im: Callable, get_prefs: Callable, screen: tuple[int, int]):
        import tkinter as tk

        self.tk = tk
        self.root = root
        self.origin = tuple(int(v) for v in origin)
        self.get_im = get_im
        self.get_prefs = get_prefs
        self.sw, self.sh = screen
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.configure(bg="#10131A")
        try:
            self.win.attributes("-topmost", True)
            self.win.attributes("-type", "utility")
        except tk.TclError:
            pass
        self.lbl = tk.Label(self.win, bd=0, highlightthickness=0, bg=GROUND)
        self.lbl.pack(side="top", fill="both", expand=True)
        self._build_bar()
        self.close_btn = tk.Label(
            self.win, text="×", font=("DejaVu Sans Mono", 14),
            fg=INK, bg=GROUND, cursor="hand2",
        )
        self.close_btn.place(relx=1.0, x=-28, y=0, width=28, height=28)
        self.close_btn.bind("<Button-1>", lambda e: self.close())
        self.close_btn.bind("<Enter>", lambda e: self.close_btn.configure(fg=EMBER2))
        self.close_btn.bind("<Leave>", lambda e: self.close_btn.configure(fg=INK))
        self.photo = None
        self.alive = True
        self._anim = False
        self._drag = None
        self._resize = None
        self._press = None
        self.box = self.origin
        self.lbl.bind("<Button-1>", self._down)
        self.lbl.bind("<B1-Motion>", self._move)
        self.lbl.bind("<ButtonRelease-1>", self._up)
        self.lbl.bind("<Double-Button-1>", lambda e: self.close())
        self.lbl.bind("<Button-3>", lambda e: self.close())
        self.lbl.bind("<Motion>", self._cursor)
        self.win.bind("<Escape>", lambda e: self.close())
        self.win.bind("<Key-Escape>", lambda e: self.close())
        self.win.bind("<Configure>", lambda e: self._paint())
        self.win.after(80, self._tick)
        self.win.after(600, self._focus)

    def open(self) -> None:
        dest = dest_box(self.sw, self.sh, self.get_prefs(), BAR_H)
        self._animate(self.origin, dest)

    def _build_bar(self) -> None:
        tk = self.tk
        self.bar = tk.Frame(self.win, bg=GROUND, height=BAR_H)
        self.bar.pack(side="bottom", fill="x")
        self.bar.pack_propagate(False)
        inner = tk.Frame(self.bar, bg=GROUND)
        inner.pack(fill="both", expand=True, padx=6, pady=4)

        def chip(text):
            lab = tk.Label(
                inner, text=text, font=FONT, fg=INK3, bg=GROUND,
                padx=7, pady=2, bd=1, relief="solid", cursor="hand2",
            )
            lab._on = False
            return lab

        def set_on(lab, on):
            lab._on = bool(on)
            lab.config(fg=EMBER if lab._on else INK3)

        self._set_on = set_on
        self.ar_b = chip("AR")
        self.ar_b.pack(side="left", padx=(0, 6))
        self.blur_b = chip("Blur")
        self.blur_b.pack(side="left", padx=(0, 6))
        self.mode_b = chip("FT")
        self.mode_b.pack(side="left", padx=(0, 10))
        minus = tk.Label(
            inner, text="−", font=FONT, fg=INK3, bg=GROUND,
            padx=5, pady=2, bd=1, relief="solid", cursor="hand2",
        )
        minus.pack(side="left")
        self.size_val = tk.Label(inner, text="1.00", font=FONT, fg=INK, bg=GROUND, width=4)
        self.size_val.pack(side="left", padx=3)
        plus = tk.Label(
            inner, text="+", font=FONT, fg=INK3, bg=GROUND,
            padx=5, pady=2, bd=1, relief="solid", cursor="hand2",
        )
        plus.pack(side="left")
        self.ar_b.bind("<Button-1>", lambda e: self._toggle("hole_ar"))
        self.blur_b.bind("<Button-1>", lambda e: self._toggle("blur"))
        self.mode_b.bind("<Button-1>", lambda e: self._toggle_mode())
        minus.bind("<Button-1>", lambda e: self._nudge_size(-prefs.SIZE_STEP))
        plus.bind("<Button-1>", lambda e: self._nudge_size(prefs.SIZE_STEP))
        self._paint_bar()

    def _toggle(self, key: str) -> None:
        p = prefs.load()
        p[key] = not p.get(key, False)
        prefs.save(p)
        self._paint_bar()
        self._paint()

    def _toggle_mode(self) -> None:
        p = prefs.load()
        p["ft_mode"] = "room" if p.get("ft_mode") != "room" else "ft"
        prefs.save(p)
        self._apply_size()
        self._paint_bar()
        self._paint()

    def _nudge_size(self, delta: float) -> None:
        prefs.nudge("ft_size", delta)
        self._apply_size()
        self._paint_bar()

    def _apply_size(self) -> None:
        if self._anim or not self.alive:
            return
        x, y, w, h = self.box
        cx, cy = x + w / 2, y + h / 2
        _, _, nw, nh = dest_box(self.sw, self.sh, self.get_prefs(), BAR_H)
        self.box = (int(cx - nw / 2), int(cy - nh / 2), nw, nh)
        self._place()
        self._paint()

    def _paint_bar(self) -> None:
        p = self.get_prefs()
        self._set_on(self.ar_b, p.get("hole_ar", True))
        self._set_on(self.blur_b, p.get("blur", False))
        self.mode_b.config(text="ROOM" if p.get("ft_mode") == "room" else "FT")
        self._set_on(self.mode_b, p.get("ft_mode") == "room")
        self.size_val.config(text=f"{float(p.get('ft_size') or 1):.2f}")

    def _place(self) -> None:
        x, y, w, h = self.box
        w, h = max(80, int(w)), max(60, int(h))
        if y + h + BAR_H > self.sh - 4:
            y = max(4, self.sh - 4 - h - BAR_H)
        if y < 4:
            y = 4
        if x + w > self.sw - 4:
            x = max(4, self.sw - 4 - w)
        if x < 4:
            x = 4
        self.box = (x, y, w, h)
        self.win.geometry(f"{w}x{h + BAR_H}+{x}+{y}")

    def close(self) -> None:
        if not self.alive or self._anim:
            return
        self._animate(self.box, self.origin, then=self._destroy)

    def _destroy(self) -> None:
        self.alive = False
        try:
            self.win.destroy()
        except Exception:
            pass

    def _animate(self, a, b, then=None, ms: int = 560) -> None:
        self._anim = True
        t0 = time.time()

        def step():
            if not self.alive:
                return
            u = min(1.0, (time.time() - t0) / (ms / 1000.0))
            self.box = lerp_box(a, b, u)
            self._place()
            try:
                self.win.attributes("-alpha", 0.55 + 0.45 * ease(u if then is None else 1 - u * 0.15))
            except Exception:
                pass
            self._paint()
            if u < 1.0:
                self.win.after(16, step)
            else:
                self._anim = False
                if then:
                    then()
                else:
                    self.box = dest_box(self.sw, self.sh, self.get_prefs(), BAR_H)
                    self._place()
                    self._paint()
                    self._paint_bar()

        self.win.deiconify()
        self._focus()
        step()

    def _focus(self) -> None:
        try:
            self.win.focus_force()
        except Exception:
            pass

    def _paint(self) -> None:
        if not self.alive:
            return
        w = max(80, self.win.winfo_width())
        h = max(60, self.win.winfo_height() - BAR_H)
        p = self.get_prefs()
        frame = None
        if p.get("hole_ar"):
            try:
                import house_ascii_shm as shm
                x, y = self.win.winfo_rootx(), self.win.winfo_rooty()
                slice_ = shm.crop_screen(x, y, w, h)
                if slice_ is not None:
                    if slice_.size != (w, h):
                        slice_ = slice_.resize((w, h), Image.Resampling.BILINEAR)
                    frame = slice_
            except Exception:
                frame = None
        if frame is None:
            im = self.get_im()
            if im is None:
                return
            frame = crop_frame(im, p, (w, h))
        self.photo = ImageTk.PhotoImage(frame)
        self.lbl.configure(image=self.photo)
        try:
            self.close_btn.lift()
            self.bar.lift()
        except Exception:
            pass

    def _tick(self) -> None:
        if not self.alive:
            return
        if not self._anim:
            self._paint()
            self._paint_bar()
        self.win.after(80, self._tick)

    def _pic_size(self) -> tuple[int, int]:
        return max(80, self.win.winfo_width()), max(60, self.win.winfo_height() - BAR_H)

    def _edge(self, x, y, w, h) -> str | None:
        m = 8
        left, right, top, bot = x < m, x > w - m, y < m, y > h - m
        if top and left:
            return "nw"
        if top and right:
            return "ne"
        if bot and left:
            return "sw"
        if bot and right:
            return "se"
        if left:
            return "w"
        if right:
            return "e"
        if top:
            return "n"
        if bot:
            return "s"
        return None

    def _cursor(self, ev) -> None:
        w, h = self._pic_size()
        e = self._edge(ev.x, ev.y, w, h)
        self.win.configure(cursor=e + "_resize" if e and len(e) == 1 else ("sizing" if e else "hand2"))

    def _down(self, ev) -> None:
        if self._anim:
            return
        w, h = self._pic_size()
        # top-right 28px is close, not drag
        if ev.x >= w - 28 and ev.y <= 28:
            self.close()
            return
        e = self._edge(ev.x, ev.y, w, h)
        gx, gy = self.win.winfo_x(), self.win.winfo_y()
        self._press = (ev.x_root, ev.y_root)
        if e:
            self._resize = (e, ev.x_root, ev.y_root, gx, gy, w, h)
            self._drag = None
        else:
            self._drag = (ev.x_root - gx, ev.y_root - gy)
            self._resize = None

    def _move(self, ev) -> None:
        if self._anim:
            return
        if self._drag:
            dx, dy = self._drag
            pw, ph = self._pic_size()
            self.box = (ev.x_root - dx, ev.y_root - dy, pw, ph)
            self._place()
            return
        if not self._resize:
            return
        kind, x0, y0, gx, gy, w, h = self._resize
        dx, dy = ev.x_root - x0, ev.y_root - y0
        nx, ny, nw, nh = gx, gy, w, h
        if "e" in kind:
            nw = max(160, w + dx)
        if "s" in kind:
            nh = max(120, h + dy)
        if "w" in kind:
            nw = max(160, w - dx)
            nx = gx + (w - nw)
        if "n" in kind:
            nh = max(120, h - dy)
            ny = gy + (h - nh)
        self.box = (nx, ny, nw, nh)
        self._place()

    def _up(self, ev) -> None:
        press = self._press
        was_drag = self._drag is not None
        self._drag = None
        self._resize = None
        self._press = None
        x, y = self.win.winfo_x(), self.win.winfo_y()
        pw, ph = self._pic_size()
        self.box = (x, y, pw, ph)
        # click (no drag) closes — same as the kiosk FT pop. Drag still moves.
        if was_drag and press:
            dx = abs(ev.x_root - press[0])
            dy = abs(ev.y_root - press[1])
            if dx < 8 and dy < 8:
                self.close()
