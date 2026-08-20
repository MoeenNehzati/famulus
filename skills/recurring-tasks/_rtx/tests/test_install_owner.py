#!/usr/bin/env python3
"""Tests for _install_owner: which checkout owns the installed registrations.

The machine has one installation and N checkouts of this skill (canonical, plus
every git worktree). Nothing recorded which checkout owned the installation, so
a sync run from any copy silently repointed it -- see the 2026-08-17 incident,
where a sync inside a worktree repointed the health-check cron entry and it then
reported ``service unit stale`` every four hours against a healthy install.
"""
import tempfile
from pathlib import Path

from .. import _install_owner as install_owner


def test_sync_is_allowed_when_the_record_names_this_checkout(tmp_path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    skill_dir = Path.home() / "canonical" / "_rtx"
    install_owner.write_owner(unit_dir=unit_dir, owner=skill_dir)

    install_owner.require_ownership(
        unit_dir=unit_dir, skill_dir=skill_dir, registrations_present=True
    )


def test_sync_is_refused_when_the_record_names_a_different_checkout(tmp_path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    owner = Path.home() / "canonical" / "_rtx"
    worktree = Path.home() / "worktrees" / "flaky-test-triage" / "_rtx"
    install_owner.write_owner(unit_dir=unit_dir, owner=owner)

    try:
        install_owner.require_ownership(
            unit_dir=unit_dir, skill_dir=worktree, registrations_present=True
        )
    except install_owner.NotTheOwnerError as exc:
        # Both paths must appear: the operator has to see which copy owns the
        # installation and which one they are actually running from.
        assert str(owner) in str(exc)
        assert str(worktree) in str(exc)
    else:
        raise AssertionError("a non-owning checkout must not be allowed to sync")


def test_a_fresh_install_adopts_when_there_is_no_record_and_nothing_installed(tmp_path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()

    install_owner.require_ownership(
        unit_dir=unit_dir,
        skill_dir=Path.home() / "canonical" / "_rtx",
        registrations_present=False,
    )


def test_a_missing_record_does_not_disarm_the_guard_when_registrations_exist(tmp_path):
    """A missing record must not read as "fresh install" when units are there.

    Otherwise deleting one file silently restores the original defect: the next
    sync from any checkout adopts, repoints the installation, and exits 0.
    """
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()

    try:
        install_owner.require_ownership(
            unit_dir=unit_dir,
            skill_dir=Path.home() / "worktree" / "_rtx",
            registrations_present=True,
        )
    except install_owner.NotTheOwnerError:
        pass
    else:
        raise AssertionError("a missing record must not authorize writing over an install")


def test_an_unreadable_record_is_treated_as_missing_rather_than_raising(tmp_path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    install_owner.record_path(unit_dir).write_text("{ truncated", encoding="utf-8")

    assert install_owner.read_owner(unit_dir) is None


def test_a_temporary_copy_cannot_become_the_owner_even_on_a_fresh_install(tmp_path):
    """Ownership covers an existing install; this covers a fresh one.

    The repository checks run validators from a mirror under the temp
    directory, which is then deleted. On a host with nothing installed yet that
    mirror would otherwise adopt, and the health-check cron entry would be
    written pointing into a directory that is about to disappear -- the
    original failure, reached by a different route.
    """
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    mirror = Path(tempfile.gettempdir()).resolve() / "mirror" / "_rtx"

    try:
        install_owner.require_ownership(
            unit_dir=unit_dir, skill_dir=mirror, registrations_present=False
        )
    except install_owner.NotTheOwnerError as exc:
        assert "temporary" in str(exc).lower()
    else:
        raise AssertionError("a temp-directory copy must not become the owner")


def test_adopt_moves_the_installation_past_a_disagreeing_record(tmp_path):
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    install_owner.write_owner(unit_dir=unit_dir, owner=Path.home() / "canonical" / "_rtx")

    install_owner.require_ownership(
        unit_dir=unit_dir,
        skill_dir=Path.home() / "worktree" / "_rtx",
        registrations_present=True,
        adopt=True,
    )
