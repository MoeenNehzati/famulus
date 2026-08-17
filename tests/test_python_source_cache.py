"""Tests for session-local Python source and AST preparation."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import officina.common.python_source_cache as cache_module
from officina.common.python_source_cache import PythonSourceCache


def test_repeated_exact_path_reads_and_parses_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "module.py"
    path.write_text("value = 1\n", encoding="utf-8")
    original_parse = cache_module.ast.parse
    parse_calls: list[str] = []

    def counting_parse(source: str, *, filename: str):
        parse_calls.append(filename)
        return original_parse(source, filename=filename)

    monkeypatch.setattr(cache_module.ast, "parse", counting_parse)
    cache = PythonSourceCache(tmp_path)

    first_source, first_tree = cache.read_parse(path)
    second_source, second_tree = cache.read_parse(path)

    assert first_source == second_source == "value = 1\n"
    assert first_tree is second_tree
    assert parse_calls == [str(path)]


def test_symlink_aliases_remain_distinct_cache_keys(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "module.py"
    alias = tmp_path / "alias.py"
    path.write_text("value = 1\n", encoding="utf-8")
    alias.symlink_to(path)
    original_parse = cache_module.ast.parse
    parse_calls: list[str] = []

    def counting_parse(source: str, *, filename: str):
        parse_calls.append(filename)
        return original_parse(source, filename=filename)

    monkeypatch.setattr(cache_module.ast, "parse", counting_parse)
    cache = PythonSourceCache(tmp_path)

    cache.read_parse(path)
    cache.read_parse(alias)

    assert parse_calls == [str(path), str(alias)]


def test_relative_and_absolute_spellings_share_one_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    absolute = tmp_path / "module.py"
    absolute.write_text("value = 1\n", encoding="utf-8")
    original_parse = cache_module.ast.parse
    parse_calls: list[str] = []

    def counting_parse(source: str, *, filename: str):
        parse_calls.append(filename)
        return original_parse(source, filename=filename)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cache_module.ast, "parse", counting_parse)
    cache = PythonSourceCache(tmp_path)

    cache.read_parse(Path("module.py"))
    cache.read_parse(absolute)

    assert parse_calls == [str(absolute)]


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("syntax.py", b"if:\n", SyntaxError),
        ("unicode.py", b"\xff", UnicodeDecodeError),
    ],
)
def test_cached_parse_failures_are_replayed_as_distinct_exceptions(
    tmp_path: Path,
    monkeypatch,
    name: str,
    content: bytes,
    expected: type[BaseException],
) -> None:
    path = tmp_path / name
    path.write_bytes(content)
    original_read_text = Path.read_text
    reads = 0

    def counting_read_text(candidate: Path, *args, **kwargs):
        nonlocal reads
        if candidate == path:
            reads += 1
        return original_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    cache = PythonSourceCache(tmp_path)

    with pytest.raises(expected) as first:
        cache.read_parse(path)
    with pytest.raises(expected) as second:
        cache.read_parse(path)

    assert reads == 1
    assert first.value is not second.value
    assert type(first.value) is type(second.value) is expected
    assert first.value.args == second.value.args
    assert str(first.value) == str(second.value)
    if isinstance(first.value, SyntaxError):
        assert first.value.filename == second.value.filename == str(path)
        assert (
            first.value.lineno,
            first.value.offset,
            first.value.text,
            first.value.end_lineno,
            first.value.end_offset,
        ) == (
            second.value.lineno,
            second.value.offset,
            second.value.text,
            second.value.end_lineno,
            second.value.end_offset,
        )
    if isinstance(first.value, UnicodeDecodeError):
        assert (
            first.value.encoding,
            first.value.object,
            first.value.start,
            first.value.end,
            first.value.reason,
        ) == (
            second.value.encoding,
            second.value.object,
            second.value.start,
            second.value.end,
            second.value.reason,
        )


def test_cached_os_error_is_replayed_without_rereading(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "missing.py"
    original_read_text = Path.read_text
    reads = 0

    def counting_read_text(candidate: Path, *args, **kwargs):
        nonlocal reads
        if candidate == path:
            reads += 1
        return original_read_text(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    cache = PythonSourceCache(tmp_path)

    with pytest.raises(OSError) as first:
        cache.read_parse(path)
    with pytest.raises(OSError) as second:
        cache.read_parse(path)

    assert reads == 1
    assert first.value is not second.value
    assert type(first.value) is type(second.value) is FileNotFoundError
    assert first.value.args == second.value.args
    assert str(first.value) == str(second.value)
    assert first.value.errno == second.value.errno
    assert first.value.filename == second.value.filename == str(path)
