"""
cozy/power_mode.py - B1 (particles + shake + combo counter) and
C7 (combo glow at 25x / 50x / 100x).

Components:
    ParticleOverlay     transparent full-window canvas; bursts at the caret
    ScreenShake         1-3 px jitter around the window; restores exact pos
    ComboTracker        editor keypress counter; 2s silence resets it
    ComboGlow           animated border glow (QVariantAnimation on
                        valueChanged - no custom property needed)
    PowerModeController the only class main.py talks to

Caret mapping reuses the same SendScintilla trio as the signature-tooltip
fix: 2008 (SCI_GETCURRENTPOS), 2164/2165 (POINTX/YFROMPOSITION).

WHY THE PARTICLES WERE NOT AT THE CARET (two bugs, both fixed here):

1. TIMING — the tracker's event filter runs when the key event is
   DISPATCHED, i.e. BEFORE QsciScintilla has inserted the character.
   Reading the caret at that moment returns the PRE-insertion position:
   one character to the left on normal typing, and (much more visibly)
   the END OF THE PREVIOUS LINE when Enter is pressed, because the caret
   only moves to the new line after the key is processed. Fix: the burst
   is deferred with QTimer.singleShot(0) — it runs on the next event-loop
   iteration, after the character was inserted, wrap/auto-indent ran, and
   the caret sits at its final position.

2. WRONG EDITOR — the old code burst around self._editor, the editor
   attached by the textChanged hook. That reference is one keystroke
   stale: the FIRST character typed after switching tabs or split panes
   burst around the PREVIOUS editor's caret (clearly visible in a split
   workspace). Fix: the keystroke signal now carries the widget that
   actually RECEIVED the key event (the filter sees it), so the burst
   always maps the caret of the editor being typed in.

Additionally the x coordinate is calibrated at runtime for the
line-number margin: depending on the QScintilla build, POINTXFROMPOSITION
returns either widget-relative x (margin included) or text-area-relative
x (margin missing). The calibration measures both once per burst and
adds the margin width only when it is actually missing.

WHY THE PARTICLES SAT AT THE START OF LINE 1 (the screenshot bug):
    Scintilla's SCI_POINTXFROMPOSITION / SCI_POINTYFROMPOSITION take the
    position in the LPARAM slot - wParam is unused:

        SCI_POINTXFROMPOSITION(<unused>, position pos)

    The code passed the caret as the second Python argument
    (SendScintilla(2164, caret)), which PyQt5 binds to WPARAM. Scintilla
    silently ignored it and used lParam = 0 -> the coordinates of
    DOCUMENT POSITION 0 (start of line 1, top of the text area) for
    every keystroke. The correct call passes the caret as LPARAM:

        editor.SendScintilla(2164, 0, caret)
        editor.SendScintilla(2165, 0, caret)

    NOTE: pythoneditor.py's signature tooltip (_on_signature_ready) has
    the same latent bug - it also shows at the top-left corner.
"""

import random

from PyQt5.QtCore import (Qt, QTimer, QObject, QEvent, QPoint, QPointF,
                          QRectF, QVariantAnimation, pyqtSignal)
from PyQt5.QtGui import QPainter, QColor, QPen, QBrush
from PyQt5.QtWidgets import QWidget, QApplication

# --------------------------------------------------------------------------------------------- #
# B1 Particles
# --------------------------------------------------------------------------------------------- #
class Particle:
    """One dot: position, velocity, remaining life in ticks, color."""

    __slots__ = ("x", "y", "vx", "vy", "life", "color")

    def __init__(self, x, y, color):
        self.x, self.y = x, y
        self.vx = random.uniform(-2.2, 2.2)     # random sideways kick
        self.vy = random.uniform(-3.5, 0.5)     # mostly upward = spark
        self.life = random.randint(8, 18)    # tick @ 30ms = 240-540ms
        self.color = color

class ParticleOverlay(QWidget):
    """
    Transparent full-window canvas. Never intercepts mouse events
    (WA_TransparentForMouseEvents), so the editor keeps working "through"
    the particles, sync_geometry keeps it exactly covering the window.
    """

    # your editor's palette - particles match the syntax colors
    COLORS = ["#FF5700", "#00FF21", "#0082FF", "#EA5A5A", "#11E4F6"]

    MAX_PARTICLES = 200   # CPU-spike cap: a runaway burst drops the oldest

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._particles = []
        self.sync_geometry()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(30)                   # ~33 fps, plenty for confetti

    def sync_geometry(self):
        """Cover the parent window exactly (called at startup and on every
        window resize — see PowerModeController.eventFilter)."""
        self.setGeometry(self.parentWidget().rect())
        self.raise_()

    def burst(self, x, y, count=6):
        """Spawn `count` particles at overlay-local (x, y)."""
        for _ in range(count):
            self._particles.append(Particle(x, y, QColor(random.choice(self.COLORS))))
            # Cap: drop the OLDEST dots so one stuck burst can never grow
            # the list (and the paint cost) without bound.
            if len(self._particles) > self.MAX_PARTICLES:
                self._particles.pop(0)
        self.raise_()

    def _step(self):
        """Advanced physics one tick; repaint only when particles exist."""
        if not self._particles:
            return

        for p in self._particles:
            p.x += p.vx
            p.y += p.vy
            p.vy += 0.25           # gravity
            p.life -= 1
        self._particles = [p for p in self._particles if p.life > 0]
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for p in self._particles:
            c = QColor(p.color)
            c.setAlpha(int(155 * p.life / 18))      # fade out as life runs down
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(c))
            painter.drawEllipse(QPointF(p.x, p.y), 2.2, 2.2)
        painter.end()

    def resizeEvent(self, e):
        self.setGeometry(self.parentWidget().rect())
        super().resizeEvent(e)

# --------------------------------------------------------------------------------------------- #
#   B1: Screen Shake
# --------------------------------------------------------------------------------------------- #
class ScreenShake:
    """
    1-3 px shake around the window; restore the EXACT original position.

    The original position is re-captured on every shake() call (never
    cached across shakes) - caching would restore to a stale position if
    the user moved or maximized the window between shakes.
    """

    JOLTS = 6   # number of 30ms ticks per shake

    def __init__(self, widget, intensity=1):
        self._widget = widget
        self._intensity = intensity
        self._orig = widget.pos()
        self._ticks = 0
        self._timer = QTimer(widget)
        self._timer.timeout.connect(self._jolt)

    def set_intensity(self, intensity):
        """Runtime intensity change (Settings dialog hook). 0 = off."""
        self._intensity = intensity

    def shake(self):
        """Six 30ms jolts = 180ms total. No-op at intensity 0."""
        if self._intensity <= 0:
            return

        self._orig = self._widget.pos()
        self._ticks = self.JOLTS
        self._timer.start(30)

    def _jolt(self):
        self._ticks -= 1
        if self._ticks <= 0:
            self._timer.stop()
            self._widget.move(self._orig)
            return
        dx = random.randint(-self._intensity, self._intensity)
        dy = random.randint(-self._intensity, self._intensity)
        self._widget.move(self._orig.x() + dx, self._orig.y() + dy)

# --------------------------------------------------------------------------------------------- #
#   B1: Combo Tracker | C7: glow
# --------------------------------------------------------------------------------------------- #
class ComboTracker(QObject):
    """
    Counts consecutive keystrokes typed into EDITORS; resets after
    RESET_MS silence.

    Only KeyPress events that PRODUCE TEXT count — arrows, Ctrl+S,
    Shift+Home are free (they don't advance a combo). Installed on the
    QApplication so every editor widget feeds it without per-editor code.

    The keystroke signal carries the widget that RECEIVED the key event,
    so the particle burst can map the caret of the editor that is actually
    being typed in — never a stale editor reference. Keys typed into
    non-editor widgets (search box, settings fields) neither advance the
    combo nor spawn particles.
    """

    RESET_MS = 2000
    keystroke = pyqtSignal(QObject)       # the editor the key went to
    combo_milestone = pyqtSignal(int)     # exactly x25 / x50 / x100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._count = 0
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._reset)

    def _reset(self):
        self._count = 0

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and event.text():
            # Only keys typed into a Scintilla editor count (duck-typed:
            # QLineEdit/QSpinBox & friends have no SendScintilla).
            if hasattr(obj, "SendScintilla"):
                self._count += 1
                self._idle.start(self.RESET_MS)
                self.keystroke.emit(obj)
                if self._count in (25, 50, 100):
                    self.combo_milestone.emit(self._count)
        return False


class ComboGlow(QWidget):
    """
    C7: soft border glow — quick fade in, slow fade out.

    QVariantAnimation drives the alpha directly through its valueChanged
    signal — no custom pyqtProperty needed.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._alpha = 0.0
        self.sync_geometry()

        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._set_alpha)

    def sync_geometry(self):
        """Cover the parent window exactly (see ParticleOverlay)."""
        self.setGeometry(self.parentWidget().rect())
        self.raise_()

    def _set_alpha(self, value):
        """valueChanged slot: store the float and repaint."""
        self._alpha = float(value)
        self.update()

    def flash(self, peak=0.55):
        """Quick in (~30% of the duration), slow out."""
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setKeyValueAt(0.3, peak)   # quick in ...
        self._anim.setEndValue(0.0)           # ... slow out
        self._anim.setDuration(1200)
        self._anim.start()

    def paintEvent(self, _):
        if self._alpha <= 0.01:
            return                       # skip painting entirely when idle
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor("#FF5700")
        color.setAlphaF(self._alpha)
        painter.setPen(QPen(color, 6))
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(3, 3, -3, -3), 8, 8)
        painter.end()

    def resizeEvent(self, e):
        self.setGeometry(self.parentWidget().rect())
        super().resizeEvent(e)


# --------------------------------------------------------------------- #
#  Controller — the only class main.py talks to
# --------------------------------------------------------------------- #
class PowerModeController(QObject):
    """
    Bundles overlay + shake + tracker + glow.

    Usage:
        self.power = PowerModeController(self)   # once, in set_up_body()
        self.power.attach_editor(editor)         # in _connect_editor()

    attach_editor() is only a FALLBACK anchor now — the keystroke signal
    carries the actual event receiver, so bursts always track the editor
    being typed in, even in split panes and right after tab switches.

    Every effect is individually switchable (View menu -> Power Mode):
        set_enabled()        master switch (particles + shake + glow)
        set_particles()      particles only
        set_shake_enabled()  screen shake only
        set_glow()           combo glow only
    """

    SCI_GETCURRENTPOS = 2008
    SCI_POINTXFROMPOSITION = 2164
    SCI_POINTYFROMPOSITION = 2165
    SCI_GETMARGINWIDTHN = 2243

    def __init__(self, window, enabled=True, shake_intensity=1,
                 glow_enabled=True, particles_on=True, shake_on=True):
        super().__init__(window)
        self._window = window
        self._enabled = enabled            # master switch
        self._glow_enabled = glow_enabled
        self._particles_on = particles_on
        self._shake_on = shake_on
        self._editor = None

        self.particles = ParticleOverlay(window)
        self.glow = ComboGlow(window)
        self.shake = ScreenShake(window, shake_intensity)
        self.combo = ComboTracker(self)

        # App-wide filter: an event filter on the WINDOW never sees the
        # keystrokes (they are delivered to the editor, not the window) —
        # that was the original dead-power-mode bug.
        QApplication.instance().installEventFilter(self.combo)

        # Keep both overlays exactly covering the window. The overlays are
        # NOT in a layout, so Qt never resizes them on its own; the
        # controller watches the window's Resize events and re-syncs.
        window.installEventFilter(self)

        self.combo.keystroke.connect(self._on_keystroke)
        self.combo.combo_milestone.connect(self._on_milestone)

    def eventFilter(self, obj, event):
        """Window Resize -> keep overlays glued to the window edges."""
        if obj is self._window and event.type() == QEvent.Resize:
            self.particles.sync_geometry()
            self.glow.sync_geometry()
        return False

    def attach_editor(self, editor):
        """Fallback anchor editor (celebratory bursts, legacy support)."""
        self._editor = editor

    # ----------------------------------------------------------------- #
    #  Burst at the caret
    # ----------------------------------------------------------------- #
    def _on_keystroke(self, editor):
        """
        A key was pressed in `editor`. The burst is DEFERRED by one
        event-loop turn: the event filter runs before QsciScintilla has
        inserted the character, so the caret is still at the
        pre-insertion spot (end of the previous line when Enter was hit).
        QTimer.singleShot(0) fires after the key was fully processed —
        character inserted, caret at its final position.
        """
        if not self._enabled or editor is None:
            return
        QTimer.singleShot(0, lambda ed=editor: self._burst_at_caret(ed))

    def _burst_at_caret(self, editor):
        """Map the caret of `editor` to window coordinates and burst."""
        try:
            caret = editor.SendScintilla(self.SCI_GETCURRENTPOS)
            # The position goes in LPARAM (the third slot); wParam is
            # unused by these two messages. Passing it as the second
            # argument binds it to WPARAM - Scintilla ignores it and
            # returns the coordinates of position 0 instead (the
            # start-of-line-1 bug).
            x = editor.SendScintilla(self.SCI_POINTXFROMPOSITION, 0, caret)
            y = editor.SendScintilla(self.SCI_POINTYFROMPOSITION, 0, caret)
            x += self._margin_calibration(editor)
            point = editor.mapTo(self._window, QPoint(x, y))
        except RuntimeError:
            # The editor's tab was closed and Qt deleted the C++ object
            # under the Python wrapper — nothing to burst around.
            return
        if self._particles_on:
            self.particles.burst(point.x(), point.y(), count=4)
        if self._shake_on:
            self.shake.shake()

    def _margin_calibration(self, editor):
        """
        Some QScintilla builds return the caret x relative to the TEXT
        AREA (line-number margin excluded), others relative to the WIDGET
        (margin included). Detect which one we have and return the missing
        offset:

          ref_x = POINTX(first document position)
          margin_total = width of all left margins

          widget-relative  -> ref_x == margin_total  -> offset 0
          text-relative    -> ref_x == 0             -> offset margin_total

        max(0, ...) keeps the calibration inert when the x already
        includes the margin, so it is safe under both conventions.
        """
        try:
            first_pos = editor.positionFromLineIndex(0, 0)
            # lParam form here too — see _burst_at_caret.
            ref_x = editor.SendScintilla(self.SCI_POINTXFROMPOSITION, 0,
                                          first_pos)

            margin_total = 0
            for m in range(8):   # Scintilla supports 8 margins max
                margin_total += editor.SendScintilla(self.SCI_GETMARGINWIDTHN, m)
            return max(0, margin_total - ref_x)
        except (RuntimeError, AttributeError):
            return 0

    def _on_milestone(self, count):
        """C7: x25 / x50 / x100 — glow the frame, brighter per milestone."""
        if self._enabled and self._glow_enabled:
            self.glow.flash(peak=0.35 + 0.1 * (count // 25))

    # --- runtime toggles (View menu -> Power Mode hooks in here) ------ #
    def set_enabled(self, on):
        """Master switch. Off also clears any in-flight effects so
        nothing keeps playing after the switch."""
        self._enabled = on
        if not on:
            self.particles._particles.clear()
            self.particles.update()
            if self.shake._timer.isActive():
                self.shake._timer.stop()
                self.shake._widget.move(self.shake._orig)
            self.glow._anim.stop()

    def set_particles(self, on):
        """Particles-only switch. Off clears dots already in flight."""
        self._particles_on = on
        if not on:
            self.particles._particles.clear()
            self.particles.update()

    def set_shake_enabled(self, on):
        """Shake-only switch. Off stops a running shake and restores
        the exact window position."""
        self._shake_on = on
        if not on and self.shake._timer.isActive():
            self.shake._timer.stop()
            self.shake._widget.move(self.shake._orig)

    def set_shake(self, intensity):
        """Shake intensity (Settings dialog hook). 0 = off."""
        self.shake.set_intensity(intensity)

    def set_glow(self, on):
        """Combo-glow-only switch. Off stops a running flash."""
        self._glow_enabled = on
        if not on:
            self.glow._anim.stop()
