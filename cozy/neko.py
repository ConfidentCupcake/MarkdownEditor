"""
cozy/neko.py - C3:
a tiny cat that walks towards your last mouse position when you idle for 30+ seconds.

A CHILD widget of the main window (not a separate OS window):
    it cllips to the editor area and never appears in the tastbar. An application-wide
    event filter watches for ANY user activity - mouse move, key press, wheel - across
    ALL widgets (terminal, tree view, settings dialog too), which is why the filter is
    installed on the QApplication instead of hooking the editor's keyPressEvent:
        one integration point, zero coupling.
        
Coordinate note (the lesson from the signature-tooltip fix):
    QCursor.pos() is GLOBAL screen space; the cat lives in the WINDOW space. 
    _wake() maps global -> window once, at wake time.
"""
import os
import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QPoint
from PyQt5.QtGui import QCursor, QPixmap
from PyQt5.QtWidgets import QLabel, QApplication

DISPLAY_SIZE = 64
IDLE_MS = 30 * 1000      # 30 s of nothing -> cat wakes up
STEP_PX = 6              # px per 80 ms tick -> calm stroll, not a dash

ASCII_WALK = [" =( o.o )>", "<( o.o )= "]   # ASCII; swap for PNGs later
ASCII_NAP = " ( -w- ) zzz" 

def _resource_path(relative_path):
    """PyInstaller-safe path helper (same contract as main.py's)."""
    root = (Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS")
            else Path(__file__).resolve().parent.parent)
    return str(root / relative_path)


class NekoChaser(QLabel):
    """Created once from main.py; manages its own visibility."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # never blocks clicks
        self.setFixedSize(DISPLAY_SIZE, DISPLAY_SIZE)
        self.setScaledContents(False)
        self.hide()

        self._target = None
        self._frame = 0

        # --- sprite sheets (v3): walk for the stroll, sleep on arrival -- #
        self._walk_sheet, self._walk_n = self._load_sheet("walk")
        self._sleep_sheet, self._sleep_n = self._load_sheet("sleep")

        # single-shot idle timer: armed by start(), re-armed on every
        # activity event, fires once when 30 s pass with nothing
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._wake)

        # walk clock: one 80 ms tick per STEP_PX toward the target
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._step)

        # nap clock: slow 400 ms cycle through the sleep sheet AFTER the
        # cat has arrived (breathing animation, not a sprint)
        self._nap_timer = QTimer(self)
        self._nap_timer.timeout.connect(self._nap_tick)

        self._filter_installed = False

    # ----------------------------------------------------------------- #
    #  Sprite sheet handling — the same pattern CatController uses
    # ----------------------------------------------------------------- #
    def _load_sheet(self, state):
        """
        Load icons/cat_sheets/<state>.png as ONE strip.

        :return: (QPixmap, frame_count) or (None, 0) when the file is
                 missing — callers fall back to ASCII, never crash.
        """
        path = _resource_path(os.path.join("icons", "cat_sheets",
                                           state + ".png"))
        if not os.path.isfile(path):
            return None, 0
        sheet = QPixmap(path)
        if sheet.isNull():
            return None, 0
        cell = sheet.height()               # square cells
        return sheet, max(1, sheet.width() // cell)

    def _show_sheet_frame(self, sheet, n, index):
        """Play frame `index` of a strip: copy its rect, scale, show."""
        cell = sheet.height()
        pix = sheet.copy((index % n) * cell, 0, cell, cell)
        self.setPixmap(pix.scaled(DISPLAY_SIZE, DISPLAY_SIZE, Qt.KeepAspectRatio, Qt.FastTransformation))

    # ----------------------------------------------------------------- #
    #  Lifecycle
    # ----------------------------------------------------------------- #
    def start(self):
        """Install the activity filter (once) and arm the idle countdown."""
        if not self._filter_installed:
            QApplication.instance().installEventFilter(self)
            self._filter_installed = True
        self._idle_timer.start(IDLE_MS)

    def eventFilter(self, obj, event):
        """
        Application-wide activity monitor.

        Any user activity hides the cat and re-arms the 30 s countdown.
        ALWAYS returns False — the event must reach its real target
        (the editor, the terminal, ...). A filter that consumes events
        here would silently break typing.
        """
        if event.type() in (event.MouseMove, event.KeyPress, event.Wheel,
                            event.MouseButtonPress):
            if self.isVisible():
                self._clock.stop()
                self._nap_timer.stop()
                self.hide()
            self._idle_timer.start(IDLE_MS)
        return False

    def _wake(self):
        """
        Idle time reached: pick the last global mouse position, map it
        into window space, clamp it inside the visible area, and start
        the stroll (walk.png from frame 0).
        """
        pos = self._window.mapFromGlobal(QCursor.pos())
        pos.setX(max(10, min(self._window.width() - 60, pos.x())))
        pos.setY(max(10, min(self._window.height() - 30, pos.y())))
        self._target = pos
        self._frame = 0
        self.move(10, self._window.height() - 60)   # enter from bottom-left
        self.show()
        self.raise_()
        self._clock.start(80)

    def _step(self):
        """
        One walk tick: move STEP_PX toward the target and play the next
        walk.png frame. On arrival, switch from walking to the sleeping
        animation (sleep.png).
        """
        if self._target is None:
            return
        delta = self._target - self.pos()
        if delta.manhattanLength() <= STEP_PX:
            # arrived: stop strolling, start the breathing nap
            self._clock.stop()
            self._frame = 0
            self._nap_timer.start(400)
            return
        length = max(1.0, (delta.x() ** 2 + delta.y() ** 2) ** 0.5)
        new = self.pos() + QPoint(
            round(delta.x() / length * STEP_PX),
            round(delta.y() / length * STEP_PX))
        self.move(new)
        self._frame += 1
        if self._walk_sheet is not None:
            self._show_sheet_frame(self._walk_sheet, self._walk_n,
                                   self._frame)
        else:
            self.setText(ASCII_WALK[self._frame % 2])   # ASCII fallback

    def _nap_tick(self):
        """Arrived: slowly cycle sleep.png — the curled-up doze loop."""
        self._frame += 1
        if self._sleep_sheet is not None:
            self._show_sheet_frame(self._sleep_sheet, self._sleep_n,
                                   self._frame)
        else:
            self.setText(ASCII_NAP)
