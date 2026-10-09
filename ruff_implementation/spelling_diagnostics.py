"""Local English/German spelling diagnostics for Python source buffers."""

from __future__ import annotations

import builtins
import io
import keyword
import re
import threading
import tokenize
from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal

from ruff_implementation.diagnostic_model import Diagnostic, DiagnosticSeverity, Position, SourceRange
from ruff_implementation.diagnostics_manager import DiagnosticsManager
from ruff_implementation.document_snapshot import DocumentSnapshot
from ruff_implementation.ruff_lsp_controller import RuffLspController

_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-zäöüß])(?=[A-ZÄÖÜ])|(?<=[A-ZÄÖÜ])(?=[A-ZÄÖÜ][a-zäöüß])"
)
_STRING_START = re.compile(r'''(?i:[rubf]*)("""|\x27\x27\x27|"|\x27)''')
_ESCAPE = re.compile(r"\\(?:N\{[^}\n]*\}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2}|[0-7]{1,3}|[^\n])")
_STANDARD_WORDS = {
    name.lower() for name in dir(builtins)
} | set(keyword.kwlist) | set(keyword.softkwlist) | {"self", "cls", "args", "kwargs"}
