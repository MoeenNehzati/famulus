"""Shared launcher-bundle primitives for the installer-local platform layer."""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from officina.common.atomic_files import (
    atomic_publish_bytes,
    normalize_publication_mode,
    read_regular_file_bytes_bounded,
)

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .._fs_links import make_link
else:
    from _fs_links import make_link
if __package__ and __package__.count('.') >= 1:
    from .._state_record import (
        InstallerMutationError,
        MutationRecorder,
        snapshot_path_state,
    )
else:
    from _state_record import InstallerMutationError, MutationRecorder, snapshot_path_state

LauncherFileMode = Literal["generate", "copy", "link"]
LauncherStatus = Literal["installed", "would-install", "unsupported", "skipped", "failed"]

DISPATCHER_WORKFLOWS = (
    "machine-interface dispatch",
    "SKILL.md interface invocation",
)
INVOKE_SKILL_WORKFLOWS = (
    "recurring automation",
    "systemd/cron skill invocation",
)
WAKEUP_WORKFLOWS = (
    "guarded LLM session wakeups",
    "wakeup scheduling and diagnostics",
)
WAKEUP_COMMANDS = ("llm-wakeup", "lw")
_MAX_LAUNCHER_SOURCE_BYTES = 1024 * 1024


def log(msg: str = "") -> None:
    """Print one launcher-install status line immediately.

    Intent
    ------
    Emit the caller-supplied status text and flush the stream before returning.

    Rationale
    ---------
    Immediate flushing preserves useful progress ordering during installer failures.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    print(msg, flush=True)


@dataclass
class LauncherInstallResult:
    """Outcome for one launcher capability that downstream workflows rely on.

    Intent
    ------
    Outcome for one launcher capability that downstream workflows rely on. The boundary coordinates name, required, status, workflows, and path through str, bool, LauncherStatus, tuple, and Path with one closed state transition.

    Rationale
    ---------
    Because Outcome for one launcher capability that downstream workflows rely on. Keep str, bool, LauncherStatus, tuple, and Path inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    name: str
    required: bool
    status: LauncherStatus
    workflows: tuple[str, ...]
    path: Path | None = None
    reason: str = ""

    def blocks_install(self) -> bool:
        """Return whether this outcome leaves a required capability unavailable.

        Intent
        ------
        Return whether this outcome leaves a required capability unavailable. The boundary coordinates closed local state through self, and bool with one closed state transition.

        Rationale
        ---------
        Because Return whether this outcome leaves a required capability unavailable. Keep self, and bool inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        return self.required and self.status in {"skipped", "failed"}


@dataclass
class LauncherFileSpec:
    """One file in a launcher bundle.

    Intent
    ------
    One file in a launcher bundle. The boundary coordinates operation_key, destination, mode, source, and content through str, Path, LauncherFileMode, and bool with one closed state transition.

    Rationale
    ---------
    Because One file in a launcher bundle. Keep str, Path, LauncherFileMode, and bool inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    operation_key: str
    destination: Path
    mode: LauncherFileMode
    source: Path | None = None
    content: str | None = None
    executable: bool = False


@dataclass
class LauncherBundleSpec:
    """A launcher entrypoint plus any helper files it needs.

    Intent
    ------
    A launcher entrypoint plus any helper files it needs. The boundary coordinates name, files, workflows, required, and unsupported_reason through str, list, LauncherFileSpec, tuple, and bool with one closed state transition.

    Rationale
    ---------
    Because A launcher entrypoint plus any helper files it needs. Keep str, list, LauncherFileSpec, tuple, and bool inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    name: str
    files: list[LauncherFileSpec]
    workflows: tuple[str, ...]
    required: bool = True
    unsupported_reason: str = ""


def write_generated_launcher_file(
    path: Path,
    content: str,
    *,
    executable: bool,
    dry_run: bool,
    recorder: MutationRecorder | None,
    operation_key: str,
    label: str,
) -> None:
    """Publish one pre-rendered launcher as an exact owned regular file.

    Intent
    ------
    Publish one pre-rendered launcher as an exact owned regular file. The boundary coordinates path, content, executable, dry_run, and recorder through InstallerMutationError, log, encode, normalize_publication_mode, hexdigest, and sha256 with 2 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because Publish one pre-rendered launcher as an exact owned regular file. Keep InstallerMutationError, log, encode, normalize_publication_mode, hexdigest, and sha256 inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .log:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Publish one pre-rendered launcher as an exact owned regular file."
    officina.common.atomic_files.atomic_publish_bytes:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Publish one pre-rendered launcher as an exact owned regular file."

    InstantiationsFromRepo
    ----------------------
    officina.common.atomic_files.normalize_publication_mode:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Publish one pre-rendered launcher as an exact owned regular file."
    """
    if not dry_run and recorder is None:
        raise InstallerMutationError(
            "live installation requires a durable mutation recorder"
        )
    if dry_run:
        log(f"Would write {label}: {path}")
        return
    assert recorder is not None
    data = content.encode("utf-8")
    mode = normalize_publication_mode(
        0o755 if executable and sys.platform != "win32" else 0o644
    )
    intended = {
        "kind": "file",
        "mode": mode,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    recorder.mutate(
        operation_key=operation_key,
        kind="file_replace",
        resource_kind="filesystem",
        resource_id=str(path.absolute()),
        intended_after=intended,
        ownership_delta={
            "action": "upsert",
            "entry": {"kind": "file", "path": str(path)},
        },
        observe=lambda: snapshot_path_state(path),
        apply=lambda pending: atomic_publish_bytes(
            path,
            data,
            allowed_root=path.parent,
            mode=mode,
            build_id=pending.mutation_id,
            expected_before=pending.expected_before,
        ),
    )
    log(f"  Wrote {label}: {path}")


def install_static_launcher_file(
    src: Path,
    dst: Path,
    *,
    mode: Literal["copy", "link"],
    dry_run: bool,
    recorder: MutationRecorder | None,
    operation_key: str,
) -> None:
    """Install a repo-owned launcher helper by copying or symlinking it.

    Intent
    ------
    Install a repo-owned launcher helper by copying or symlinking it. The boundary coordinates src, dst, mode, dry_run, and recorder through make_link, InstallerMutationError, log, snapshot_path_state, get, and read_regular_file_bytes_bounded with 6 guarded checks, and 4 typed refusals.

    Rationale
    ---------
    Because Install a repo-owned launcher helper by copying or symlinking it. Keep make_link, InstallerMutationError, log, snapshot_path_state, get, and read_regular_file_bytes_bounded inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    .log:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Install a repo-owned launcher helper by copying or symlinking it."
    officina.common.atomic_files.atomic_publish_bytes:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Install a repo-owned launcher helper by copying or symlinking it."

    InstantiationsFromRepo
    ----------------------
    officina.common.atomic_files.read_regular_file_bytes_bounded:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Install a repo-owned launcher helper by copying or symlinking it."
    """
    if mode == "link":
        make_link(
            src,
            dst,
            dry_run,
            recorder=recorder,
            operation_key=operation_key,
        )
        return
    if not dry_run and recorder is None:
        raise InstallerMutationError(
            "live installation requires a durable mutation recorder"
        )
    if dry_run:
        log(f"  Would copy launcher: {src} -> {dst}")
        return
    assert recorder is not None
    source_before = snapshot_path_state(src)
    if source_before.get("kind") != "file":
        raise InstallerMutationError(f"required launcher source is not regular: {src}")
    data = read_regular_file_bytes_bounded(
        src,
        allowed_root=src.parent,
        maximum_bytes=_MAX_LAUNCHER_SOURCE_BYTES,
    )
    if snapshot_path_state(src) != source_before:
        raise InstallerMutationError(f"required launcher source changed: {src}")
    selected_mode = source_before.get("mode")
    if isinstance(selected_mode, bool) or not isinstance(selected_mode, int):
        raise InstallerMutationError(f"required launcher source mode is invalid: {src}")
    selected_mode = normalize_publication_mode(selected_mode & 0o777)
    intended = {
        "kind": "file",
        "mode": selected_mode,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    recorder.mutate(
        operation_key=operation_key,
        kind="file_copy",
        resource_kind="filesystem",
        resource_id=str(dst.absolute()),
        intended_after=intended,
        ownership_delta={
            "action": "upsert",
            "entry": {"kind": "file", "path": str(dst)},
        },
        observe=lambda: snapshot_path_state(dst),
        apply=lambda pending: atomic_publish_bytes(
            dst,
            data,
            allowed_root=dst.parent,
            mode=selected_mode,
            build_id=pending.mutation_id,
            expected_before=pending.expected_before,
        ),
    )
    log(f"  Copied launcher: {src} -> {dst}")


class LauncherInstallerBase:
    """Base class for platform-specific launcher bundle installers.

    Intent
    ------
    Base class for platform-specific launcher bundle installers. The boundary coordinates static_launcher_mode through Literal with one closed state transition.

    Rationale
    ---------
    Because Base class for platform-specific launcher bundle installers. Keep Literal inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    static_launcher_mode: Literal["copy", "link"] = "link"

    def install_bundle(
        self,
        bundle: LauncherBundleSpec,
        *,
        dry_run: bool,
        recorder: MutationRecorder | None,
    ) -> LauncherInstallResult:
        """Within Base class for platform-specific launcher bundle installers, coordinate bundle, dry_run, recorder, and spec through InstallerMutationError, log, LauncherInstallResult, ValueError, write_generated_launcher_file.

        Intent
        ------
        Within Base class for platform-specific launcher bundle installers, coordinate bundle, dry_run, recorder, and spec through InstallerMutationError, log, LauncherInstallResult, ValueError, write_generated_launcher_file. The boundary coordinates bundle, dry_run, recorder, and spec through InstallerMutationError, log, LauncherInstallResult, ValueError, write_generated_launcher_file, and install_static_launcher_file with 6 guarded checks, 1 bounded iterations, and 4 typed refusals.

        Rationale
        ---------
        Because Within Base class for platform-specific launcher bundle installers, coordinate bundle, dry_run, recorder, and spec through InstallerMutationError, log, LauncherInstallResult, ValueError, write_generated_launcher_file. Keep InstallerMutationError, log, LauncherInstallResult, ValueError, write_generated_launcher_file, and install_static_launcher_file inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        .install_static_launcher_file:
          why:
            computes: "This computes edge is the first repository dependency used to uphold this guarantee: Within Base class for platform-specific launcher bundle installers, coordinate bundle, dry_run, recorder, and spec through InstallerMutationError, log, LauncherInstallResult, ValueError, write_generated_launcher_file."
        .log:
          why:
            computes: "This computes edge is the second repository dependency used to uphold this guarantee: Within Base class for platform-specific launcher bundle installers, coordinate bundle, dry_run, recorder, and spec through InstallerMutationError, log, LauncherInstallResult, ValueError, write_generated_launcher_file."
        .write_generated_launcher_file:
          why:
            computes: "This computes edge is the third repository dependency used to uphold this guarantee: Within Base class for platform-specific launcher bundle installers, coordinate bundle, dry_run, recorder, and spec through InstallerMutationError, log, LauncherInstallResult, ValueError, write_generated_launcher_file."

        InstantiationsFromRepo
        ----------------------
        .LauncherInstallResult:
          why:
            constructs: "This constructs edge is the number 4 repository dependency used to uphold this guarantee: Within Base class for platform-specific launcher bundle installers, coordinate bundle, dry_run, recorder, and spec through InstallerMutationError, log, LauncherInstallResult, ValueError, write_generated_launcher_file."
        """
        if not dry_run and recorder is None:
            raise InstallerMutationError(
                "live installation requires a durable mutation recorder"
            )
        if bundle.unsupported_reason:
            log(f"  SKIP: {bundle.name} ({bundle.unsupported_reason})")
            return LauncherInstallResult(
                name=bundle.name,
                required=bundle.required,
                status="unsupported",
                workflows=bundle.workflows,
                reason=bundle.unsupported_reason,
            )

        for spec in bundle.files:
            if spec.mode == "generate":
                if spec.content is None:
                    raise ValueError(f"generated launcher file needs content: {spec.destination}")
                write_generated_launcher_file(
                    spec.destination,
                    spec.content,
                    executable=spec.executable,
                    dry_run=dry_run,
                    recorder=recorder,
                    operation_key=spec.operation_key,
                    label=bundle.name,
                )
            elif spec.mode in {"copy", "link"}:
                if spec.source is None:
                    raise ValueError(f"static launcher file needs source: {spec.destination}")
                install_static_launcher_file(
                    spec.source,
                    spec.destination,
                    mode=spec.mode,
                    dry_run=dry_run,
                    recorder=recorder,
                    operation_key=spec.operation_key,
                )
            else:
                raise ValueError(f"unknown launcher file mode: {spec.mode}")

        return LauncherInstallResult(
            name=bundle.name,
            required=bundle.required,
            status="would-install" if dry_run else "installed",
            workflows=bundle.workflows,
            path=bundle.files[0].destination if bundle.files else None,
        )

    def install_agent_launcher_files(
        self,
        *,
        source_bin_dir: Path,
        bin_dir: Path,
        agent: str,
        dry_run: bool,
        recorder: MutationRecorder | None,
    ) -> None:
        """Install one platform-specific agent launcher bundle or reject unsupported hosts.

        Intent
        ------
        Within Base class for platform-specific launcher bundle installers, coordinate source_bin_dir, bin_dir, agent, dry_run, and recorder through Path, str, bool, MutationRecorder, and NotImplementedError with 1 typed ref. The boundary coordinates source_bin_dir, bin_dir, agent, dry_run, and recorder through Path, str, bool, MutationRecorder, and NotImplementedError with 1 typed refusals.

        Rationale
        ---------
        Because Within Base class for platform-specific launcher bundle installers, coordinate source_bin_dir, bin_dir, agent, dry_run, and recorder through Path, str, bool, MutationRecorder, and NotImplementedError with 1 typed ref. Keep Path, str, bool, MutationRecorder, and NotImplementedError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        raise NotImplementedError

    def install_wakeup_launcher(
        self,
        bin_dir: Path,
        dry_run: bool,
        *,
        recorder: MutationRecorder | None,
        home: Path | None = None,
    ) -> LauncherInstallResult:
        """Install the canonical wakeup command and its short alias.

        Intent
        ------
        Install the canonical wakeup command and its short alias. The boundary coordinates bin_dir, dry_run, recorder, and home through Path, bool, MutationRecorder, NotImplementedError, and LauncherInstallResult with 1 typed refusals.

        Rationale
        ---------
        Because Install the canonical wakeup command and its short alias. Keep Path, bool, MutationRecorder, NotImplementedError, and LauncherInstallResult inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        raise NotImplementedError

    def _agent_launcher_files(self, source_bin_dir: Path, bin_dir: Path, agent: str) -> list[LauncherFileSpec]:
        """Within Base class for platform-specific launcher bundle installers, coordinate source_bin_dir, bin_dir, agent, files, and bat through LauncherFileSpec, exists, append, Path, str, and agent with 1 guarded checks.

        Intent
        ------
        Within Base class for platform-specific launcher bundle installers, coordinate source_bin_dir, bin_dir, agent, files, and bat through LauncherFileSpec, exists, append, Path, str, and agent with 1 guarded checks. The boundary coordinates source_bin_dir, bin_dir, agent, files, and bat through LauncherFileSpec, exists, append, Path, str, and agent with 1 guarded checks.

        Rationale
        ---------
        Because Within Base class for platform-specific launcher bundle installers, coordinate source_bin_dir, bin_dir, agent, files, and bat through LauncherFileSpec, exists, append, Path, str, and agent with 1 guarded checks. Keep LauncherFileSpec, exists, append, Path, str, and agent inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        .LauncherFileSpec:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Within Base class for platform-specific launcher bundle installers, coordinate source_bin_dir, bin_dir, agent, files, and bat through LauncherFileSpec, exists, append, Path, str, and agent with 1 guarded checks."
        """
        files = [
            LauncherFileSpec(
                operation_key=f"launchers.agent.{agent}.command",
                source=source_bin_dir / agent,
                destination=bin_dir / agent,
                mode=self.static_launcher_mode,
            ),
            LauncherFileSpec(
                operation_key="launchers.agent.runtime-helper",
                source=source_bin_dir / "_agent_launch.py",
                destination=bin_dir / "_agent_launch.py",
                mode=self.static_launcher_mode,
            ),
        ]
        bat = source_bin_dir / f"{agent}.bat"
        if bat.exists():
            files.append(
                LauncherFileSpec(
                    operation_key=f"launchers.agent.{agent}.batch",
                    source=bat,
                    destination=bin_dir / f"{agent}.bat",
                    mode=self.static_launcher_mode,
                )
            )
        return files

    @staticmethod
    def _shell_quote_path(path: Path) -> str:
        """Within Base class for platform-specific launcher bundle installers, coordinate path through replace, Path, str, path, and staticmethod with one closed state transition.

        Intent
        ------
        Within Base class for platform-specific launcher bundle installers, coordinate path through replace, Path, str, path, and staticmethod with one closed state transition. The boundary coordinates path through replace, Path, str, path, and staticmethod with one closed state transition.

        Rationale
        ---------
        Because Within Base class for platform-specific launcher bundle installers, coordinate path through replace, Path, str, path, and staticmethod with one closed state transition. Keep replace, Path, str, path, and staticmethod inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        return str(path).replace('"', '\\"')

    @staticmethod
    def _batch_path(path: Path) -> str:
        """Within Base class for platform-specific launcher bundle installers, coordinate path through replace, Path, str, path, and staticmethod with one closed state transition.

        Intent
        ------
        Within Base class for platform-specific launcher bundle installers, coordinate path through replace, Path, str, path, and staticmethod with one closed state transition. The boundary coordinates path through replace, Path, str, path, and staticmethod with one closed state transition.

        Rationale
        ---------
        Because Within Base class for platform-specific launcher bundle installers, coordinate path through replace, Path, str, path, and staticmethod with one closed state transition. Keep replace, Path, str, path, and staticmethod inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none
        """
        return str(path).replace('"', '""')
