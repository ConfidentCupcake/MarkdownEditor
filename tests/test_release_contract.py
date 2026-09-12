"""Fast regression tests for audited pure helpers and release contracts."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import markdown
import pytest

from main import APP_VERSION, MainWindow, is_python_path
from markdown_python_editor import __version__
from markdowneditor_assets import asset_path
from python_editor.pythoneditor import PythonEditor


@pytest.mark.parametrize("suffix", [".py", ".PY", ".pyw", ".PYW", ".pyi", ".PYI"])
def test_python_suffixes(suffix):
    assert is_python_path(Path(f"sample{suffix}"))


@pytest.mark.parametrize("suffix", [".md", ".txt", ".pyi.txt", ""])
def test_non_python_suffixes(suffix):
    assert not is_python_path(Path(f"sample{suffix}"))


def test_runtime_version_uses_package_version():
    assert APP_VERSION == f"v{__version__}"


@pytest.mark.parametrize(
    "relative",
    [
        "css/style.qss",
        "icons/app-icon.png",
        "icons/close-icon.svg",
        "themes/theme.json",
    ],
)
def test_required_assets_exist(relative):
    assert Path(asset_path(relative)).is_file()


def test_asset_traversal_is_rejected():
    with pytest.raises(ValueError):
        asset_path("../pyproject.toml")


def test_markdown_preview_keeps_safe_image_and_strips_active_content():
    # The renderer needs only its Markdown converter; constructing MainWindow
    # would start unrelated GUI/process services in this pure unit test.
    renderer = SimpleNamespace(md=markdown.Markdown(extensions=["extra"]))
    source = """
![safe](https://example.com/image.png)
<img src="https://example.com/two.png" onerror="alert(1)">
<script>alert(2)</script>
[unsafe](javascript:alert(3))
"""
    result = MainWindow._render_markdown_safely(renderer, source)
    assert 'src="https://example.com/image.png"' in result
    assert 'src="https://example.com/two.png"' in result
    assert "onerror" not in result
    assert "<script" not in result
    assert "javascript:" not in result


@pytest.mark.parametrize("expression", ["^", "$", "a*", "(?=a)"])
def test_zero_length_searches_are_rejected(expression):
    with pytest.raises(ValueError, match="zero-length"):
        MainWindow._validated_matches(re.compile(expression), "abc")


def test_invalid_replacement_group_is_rejected():
    with pytest.raises(re.error):
        MainWindow._validate_regex_replacement(re.compile("(a)"), r"\2", True)


@pytest.mark.parametrize(
    "source",
    ["print", "    print", "value = print", "obj.method2", "items.append(transform"],
)
def test_signature_trigger_accepts_normal_call_contexts(source):
    assert PythonEditor._has_callable_before_parenthesis(source)


@pytest.mark.parametrize("source", ["", "123name", "obj..method", "name."])
def test_signature_trigger_rejects_invalid_contexts(source):
    assert not PythonEditor._has_callable_before_parenthesis(source)