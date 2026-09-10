"""
cozy/cat_controller.py — the status bar cat.

One QLabel living in the status bar of MainWindow. Responsibilities:

  * state machine  — 16 named states with priorities; a higher-priority
                     event can interrupt a playing state, a lower one
                     must wait (see STATE_PRIORITY)
  * animation      — a repeating 400 ms frame clock; PNG frames from
                     icons/cat/<state>_<n>.png win, ASCII frames are the
                     built-in fallback so the cat works with zero assets
  * C1  petting    — click -> wiggle; lifetime counter in QSettings;
                     emits `petted` for achievements ("Certified Cat
                     Person" at 100)
  * C2  leveling   — add_xp() grants XP, levels derive from XP thresholds,
                     `level_up` is emitted on a threshold crossing; idle
                     ASCII flair evolves with the level
  * C5  party hat  — set_party_hat() paints a small hat above the cat
                     (ASCII "^" / composited on the PNG) until turned off

Integration surface (everything main.py needs):

    self.cat = CatController(self)
    self.statusBar().addPermanentWidget(self.cat)
    self.cat.set_state("alert", 2000)   # a Ruff error appeared
    self.cat.add_xp(10)                 # a run succeeded
    self.cat.set_party_hat(True)        # a milestone happened

Design rules honored here (learned the hard way in this project):
  * never let one timer do two jobs  (frame clock vs. state timeout)
  * defensive fallbacks everywhere    (missing PNGs -> ASCII, never a crash)
  * persistence via QSettings         (same store as ruff_save_mode etc.)
"""

import os
import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QSettings, pyqtSignal
from PyQt5.QtGui import QFont, QPainter, QPixmap, QColor
from PyQt5.QtWidgets import QLabel


def _resource_path(relative_path):
    """
    PyInstaller-safe path helper (same contract as main.py's resource_path).

    :param relative_path: path relative to the app root, e.g. "icons/cat"
    :return: absolute path that works both in source runs and in a
             frozen (--onefile) build
    """
    root = (Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS")
            else Path(__file__).resolve().parent.parent)
    return str(root / relative_path)


# --------------------------------------------------------------------- #
#  Frame data
# --------------------------------------------------------------------- #
# ASCII fallbacks — used per-state whenever icons/cat/<state>_<n>.png is
# missing. Two frames per state are enough; the 400 ms clock animates them.
ASCII_FRAMES = {
    "idle":          [" =( o.o )=",    " =( -.- )="],
    "walk":          [" =( o.o )>",    "<( o.o )= "],
    "sleep":         [" ( -w- ) zzz",  " ( -w- ) zZz"],
    "sleepy":        [" =( -.- )~yawn", " (  o.o  )   "],
    "stretch":       [" \\( o.o )/",   "  ( o.o ) ~ahh"],
    "alert":         [" =( O.O )?!",   " =( O.O )!!"],
    "type_excited":  [" [::o.o::]~~",  " [::o.O::]~~"],
    "look_away":     [" =( -_- ) ...", " =( _._ ) hmm"],
    "wiggle":        [" ~( o.o )~ ",   " ~(o.o)~  "],
    "party":         [" m( o.o )m ",   " m\\(o.o)/m "],
    "lay_down":      ["   ( o.o )___ ", "   ( -.- )___ "],
    "cry":           [" =( T_T ) pff", " =( ;_; ) pff"],
    "dead":          [" =( x x ) ...  "],
    "box":           [" [= o.o =] box "],
    "box_pop":       [" [! o.o !] up! ", " [= o.o =] box "],
    "box_idle":      [" [= -.- =] zzz "],
    # v2 sheet states
    "jump":          [" ^( o.o )^",    " ^\\( o.o )/^"],
    "attack":        [" =( o.o )= fwip", " =( >w< )= FWOSS"],
    "hurt":          [" =( x.x ) oww", " =( ;_; ) ouch"],
    "run_lay":       [" =( o.o )> ...flop", "   ( -.- )___"],
}

# While a state's timer is still running, only a HIGHER priority may replace
# it. Rationale per tier:
#   6  run results / really bad news  — newest information, always wins
#   5  a brand-new Ruff error         — outranks a running process
#   4  celebration states             — level-up, excited typing
#   3  petting                        — politely waits for important things
#   0  calm states                    — interruptible by anything
STATE_PRIORITY = {
    "stretch": 6, "look_away": 6, "cry": 6, "dead": 6, "hurt": 6,
    "attack": 5,   # a brand-new Ruff error: the cat FIGHTS the bug
    "alert": 5,
    "type_excited": 4, "party": 4,
    "wiggle": 3,
    "walk": 1,
    "idle": 0, "sleep": 0, "sleepy": 0, "lay_down": 0, "run_lay": 0,
    "box": 0, "box_pop": 0, "box_idle": 0,
}

# C2: level thresholds. Pacing intent: level 1 lands within one evening
# session (~80-120 XP from typing alone), Elder Cat Deity takes about a
# week of real work. Tune here only — nothing else hardcodes the numbers.
LEVELS = [
    ("Kitten", 0),
    ("Cat", 50),
    ("Chonky Cat", 150),
    ("Cool Cat (shades)", 300),
    ("Elder Cat Deity", 600),
]

# C2: where XP comes from. Keep the numbers in ONE place (this dict) so the
# pacing stays tunable — the same "one source of truth" rule as the theme
# palette living only in theme.json.
XP_RULES = {
    "keys_per_xp": 10,   # 1 XP per 10 typed characters
    "run_success": 10,   # a Ctrl+Enter run that exits with code 0
    "ruff_fix": 5,       # the last Ruff error in a file was fixed
    "save": 1,           # any successful save
}


class CatController(QLabel):
    """
    The status bar cat.

    Signals
    -------
    level_up : pyqtSignal(int, str)
        Emitted when an add_xp() call crosses a level threshold.
        Arguments: (new level index, level title). Hook toasts here.
    petted : pyqtSignal(int)
        Emitted on every pet() with the new lifetime total. Hook the
        "Certified Cat Person" achievement at 100.

    QSettings keys used
    -------------------
    cat_xp   : int — lifetime XP (C2)
    cat_pets : int — lifetime pet count (C1)
    """

    level_up = pyqtSignal(int, str)
    petted = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("CodeEditor", "CodeEditor")

        # --- C2: progress, persisted across sessions ------------------- #
        self._xp = int(self._settings.value("cat_xp", 0, type=int))
        self._pets = int(self._settings.value("cat_pets", 0, type=int))

        # --- animation state ------------------------------------------- #
        self._state = "idle"      # currently playing state name
        self._frame = 0           # animation frame counter (mod frame count)
        self._hat = False         # C5: party hat on/off
        self._png_sheets, self._png_frames = self._load_png_frames()

        self.setFont(QFont("Consolas", 9))
        self.setCursor(Qt.PointingHandCursor)   # C1: announces "pettable"
        self.setToolTip("pet me")

        # Frame clock: repeating, 400 ms — the animation heart. It NEVER
        # stops while the app runs; it only advances self._frame.
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(400)

        # State timeout: single-shot — how long the CURRENT state plays
        # before snapping back to idle. Two timers, two jobs; merging them
        # would freeze mid-blink or make states permanent (see docstring).
        self._state_timer = QTimer(self)
        self._state_timer.setSingleShot(True)
        self._state_timer.timeout.connect(lambda: self.set_state("idle"))

        self._render()

    # ----------------------------------------------------------------- #
    #  Public API
    # ----------------------------------------------------------------- #
    def set_state(self, state, duration_ms=2500):
        """
        Switch the cat to `state` and auto-return to idle after
        `duration_ms`.

        Priority guard: while a state is still playing (its timer active),
        only a higher-priority state may replace it. A finished run
        (stretch, 6) therefore interrupts an old Ruff alert (5); petting
        (3) waits for the alert to finish.

        :param state: key of ASCII_FRAMES / the PNG folder names
        :param duration_ms: how long before returning to idle;
                            use a large value for states that are
                            explicitly ended elsewhere (e.g. 60_000 for
                            type_excited while a process runs)
        """
        if state not in ASCII_FRAMES:
            return
        if (STATE_PRIORITY.get(state, 0) < STATE_PRIORITY.get(self._state, 0)
                and self._state_timer.isActive()):
            return
        self._state, self._frame = state, 0
        self._state_timer.start(duration_ms)
        self._render()

    def pet(self):
        """C1: wiggle + lifetime count + `petted` emission."""
        self._pets += 1
        self._settings.setValue("cat_pets", self._pets)
        self.petted.emit(self._pets)
        self.set_state("wiggle", 900)

    def add_xp(self, amount):
        """
        C2: grant XP; emits `level_up` when a threshold is crossed.

        :param amount: XP to add (see XP_RULES for the intended values)
        """
        before = self.level
        self._xp += amount
        self._settings.setValue("cat_xp", self._xp)
        if self.level > before:
            self.level_up.emit(self.level, LEVELS[self.level][0])
            self.set_state("party", 3000)     # Dance frames = level-up party

    @property
    def level(self):
        """C2: current level index derived from XP (0 = Kitten)."""
        lvl = 0
        for i, (_, needed) in enumerate(LEVELS):
            if self._xp >= needed:
                lvl = i
        return lvl

    def set_party_hat(self, on):
        """C5: paint the party hat until turned off. Session-scoped by
        design — persistent hats stop feeling earned."""
        self._hat = on
        self._render()

    # ----------------------------------------------------------------- #
    #  Internals
    # ----------------------------------------------------------------- #
    def mousePressEvent(self, event):
        """C1: the whole label is one big pet button."""
        if event.button() == Qt.LeftButton:
            self.pet()

    def _load_png_frames(self):
        """
        Scan sprite sources once. Two formats, in priority order:

        1. SHEET MODE (preferred, v2 sprites): icons/cat_sheets/<state>.png
           is ONE strip holding the whole animation. The controller plays
           frame i directly: rect (i*h, 0, (i+1)*h, h) with h = the
           sheet height — cells are square, so frame count = width // h.
           No per-frame files, no alignment bookkeeping, and interior
           pixels (eyes/mouth/whiskers) are guaranteed untouched because
           the background was flood-filled from the sheet border only.

        2. FRAME MODE (v1 sprites): icons/cat/<state>_<n>.png, one file
           per frame. Still fully supported.

        Returns (sheets, frames): sheets as {state: QPixmap}, frames as
        {state: {index: path}}. Each state resolves sheet-first, then
        frames, then ASCII — individually, never a crash.
        """
        sheets = {}
        d = _resource_path(os.path.join("icons", "cat_sheets"))
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".png"):
                    sheets[f[:-4]] = QPixmap(os.path.join(d, f))
        frames = {}
        d = _resource_path(os.path.join("icons", "cat"))
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".png"):
                    state, n = f[:-4].rsplit("_", 1)
                    try:
                        frames.setdefault(state, {})[int(n)] = os.path.join(d, f)
                    except ValueError:
                        continue   # e.g. "party_hat.png" — not a frame, skip
        return sheets, frames

    def _tick(self):
        """Frame clock callback: advance the frame counter and repaint."""
        self._frame += 1
        self._render()

    def _render(self):
        """
        Paint the current frame — PNG if available for this state,
        ASCII otherwise. C5 hat is composed in both paths; C2 idle flair
        (shades / crown / halo) decorates the ASCII idle cat by level.
        """
        # 1. SHEET MODE: play the right rect of the full strip
        sheet = self._png_sheets.get(self._state)
        if sheet is not None and not sheet.isNull():
            cell = sheet.height()               # square cells
            n = max(1, sheet.width() // cell)
            pix = sheet.copy((self._frame % n) * cell, 0, cell, cell)
            if self._hat:
                painter = QPainter(pix)
                painter.setPen(QColor("#FFD700"))
                painter.drawText(4, 12, "^")
                painter.end()
            self.setPixmap(pix.scaled(24, 24, Qt.KeepAspectRatio))
            return

        # 2. FRAME MODE: one file per frame (v1 sprites)
        pngs = self._png_frames.get(self._state)
        if pngs:
            pix = QPixmap(pngs[self._frame % len(pngs)])
            if self._hat:
                painter = QPainter(pix)
                painter.setPen(QColor("#FFD700"))
                painter.drawText(2, 6, "^")
                painter.end()
            self.setPixmap(pix.scaled(24, 24, Qt.KeepAspectRatio))
            return

        # 3. ASCII fallback
        # C2: idle ASCII cat earns flair as it levels
        flair = ["", "", " 8)", " [w]", " (o)"][min(self.level, 4)]
        hat = "^ " if self._hat else ""
        frames = ASCII_FRAMES[self._state]
        if self._state == "idle" and flair:
            frames = [frames[0] + flair, frames[1]]
        self.setText(hat + frames[self._frame % len(frames)])
