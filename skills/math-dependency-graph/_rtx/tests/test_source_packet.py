#!/usr/bin/env python3
"""Tests for lossless, non-semantic TeX source packet preparation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_SRC = SKILL_DIR.parents[2] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR))

from _source_packet import collect_source_packet, default_output_path, write_source_packet  # noqa: E402


def script_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (env.get("PYTHONPATH"), str(REPO_SRC)) if part
    )
    return env


def test_collects_nested_inputs_in_expansion_order_with_exact_locations() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        nested = root / "sections"
        nested.mkdir()
        (root / "main.tex").write_text(
            "Root before.\n"
            "\\input{sections/first}\n"
            "Root after.\n",
            encoding="utf-8",
        )
        (nested / "first.tex").write_text(
            "First before.\n"
            "% \\input{ignored}\n"
            "\\include{second}\n"
            "First after.\n",
            encoding="utf-8",
        )
        (nested / "second.tex").write_text("Second body.\n", encoding="utf-8")

        packet = collect_source_packet(root / "main.tex")

        assert packet.files == (
            (root / "main.tex").resolve(),
            (nested / "first.tex").resolve(),
            (nested / "second.tex").resolve(),
        )
        assert packet.unresolved == ()
        assert packet.text.index("0002 | \\input{sections/first}") < packet.text.index("0001 | First before.")
        assert packet.text.index("0003 | \\include{second}") < packet.text.index("0001 | Second body.")
        assert packet.text.index("0001 | Second body.") < packet.text.index("0004 | First after.")
        assert packet.text.index("0004 | First after.") < packet.text.index("0003 | Root after.")
        assert "@@ source: sections/first.tex" in packet.text
        assert "@@ source: sections/second.tex" in packet.text
        assert "ignored.tex" not in packet.text
        assert "% \\input{ignored}" not in packet.text
        assert "0002 | " in packet.text


def test_cycle_is_reported_without_recursing_forever() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.tex").write_text("\\input{child}\n", encoding="utf-8")
        (root / "child.tex").write_text("\\input{main}\n", encoding="utf-8")

        packet = collect_source_packet(root / "main.tex")

        assert len(packet.files) == 2
        assert packet.cycles == ("child.tex:1 -> main.tex",)


def test_packet_lists_syntactic_visible_environment_anchors() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.tex").write_text(
            "\\begin{definition}A definition.\\end{definition}\n"
            "\\begin{restatable}[Named]{theorem}{mainthm}A theorem.\\end{restatable}\n"
            "\\begin{proof}Not a graph block.\\end{proof}\n",
            encoding="utf-8",
        )

        packet = collect_source_packet(root / "main.tex")

        assert packet.visible_environment_anchors == (
            "main.tex:1-1 environment=definition",
            "main.tex:2-2 environment=theorem wrapper=restatable",
        )
        assert "# visible-environment-anchor-count: 2" in packet.text
        assert "# visible-environment-anchor: main.tex:1-1 environment=definition" in packet.text


def test_missing_input_fails_before_writing_packet() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        entrypoint = root / "main.tex"
        entrypoint.write_text("\\input{missing}\n", encoding="utf-8")
        out = root / "packet.txt"

        packet = collect_source_packet(entrypoint)

        assert packet.unresolved == ("main.tex:1 -> missing",)
        with pytest.raises(ValueError, match="unresolved TeX inputs"):
            write_source_packet(packet, out)
        assert not out.exists()


def test_cli_writes_default_packet_and_machine_report() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        entrypoint = root / "main.tex"
        entrypoint.write_text("Document math is $x+y$.\n", encoding="utf-8")
        expected = default_output_path(entrypoint)

        result = subprocess.run(
            [sys.executable, str(SKILL_DIR / "_source_packet.py"), str(entrypoint)],
            check=True,
            text=True,
            capture_output=True,
            env=script_env(),
        )
        report = json.loads(result.stdout)

        assert Path(report["out"]) == expected
        assert report["files"] == 1
        assert report["source_lines"] == 1
        assert report["unresolved"] == []
        assert expected.exists()
        written = expected.read_text(encoding="utf-8")
        assert "@@ source: main.tex" in written
        assert "0001 | Document math is $x+y$." in written
