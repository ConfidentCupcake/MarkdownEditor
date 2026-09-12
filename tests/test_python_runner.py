"""Qt event-loop tests for PythonRunner process replacement."""

from __future__ import annotations

import sys

from python_editor.python_runner import PythonRunner


def test_replacement_process_survives_predecessor_kill_timeout(qtbot):
    """A's stale escalation timer must never kill replacement process B."""

    runner = PythonRunner()

    runner.set_interpreter(sys.executable)

    output = []

    runner.output_ready.connect(output.append)

    runner.run_code("import time; time.sleep(0.25)")

    qtbot.waitUntil(runner.is_running, timeout=2_000)

    # Queue B while A still owns the QProcess. B announces itself and remains

    # alive beyond A's old two-second escalation deadline.

    runner.run_code("import time; print('SECOND_STARTED', flush=True); time.sleep(4)")

    qtbot.waitUntil(lambda: "SECOND_STARTED" in "".join(output), timeout=3_000)

    qtbot.wait(2_200)

    assert runner.is_running()

    runner.stop()

    qtbot.waitUntil(lambda: not runner.is_running(), timeout=3_000)
