"""Windows launcher bundle installer."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

if __package__ and __package__.count('.') >= 1:
    from .._state_record import MutationRecorder
else:
    from _state_record import MutationRecorder

from officina.common.famulus_paths import resolve_famulus_paths

from ._base_launcher import (
    DISPATCHER_WORKFLOWS,
    INVOKE_SKILL_WORKFLOWS,
    WAKEUP_COMMANDS,
    WAKEUP_WORKFLOWS,
    LauncherBundleSpec,
    LauncherFileSpec,
    LauncherInstallResult,
    LauncherInstallerBase,
    log,
)

# Fixed, immutable location of the stable launch resolver beneath a given
# runtime_root. The file deployed there is officina.install.resolvers.launch's
# source (a dependency-free, stdlib-only script). Generated shims invoke
# this path instead of embedding a release-specific repo checkout or
# interpreter: this path does not change when the repo moves or a new
# release is activated.
_RESOLVER_RELATIVE_PATH = ("bootstrap", "resolvers", "v1", "launch.py")


class WindowsPythonNotFoundError(RuntimeError):
    """Raised when no ``python``/``py`` interpreter can be resolved on PATH.

    Intent
    ------
    Raised when no ``python``/``py`` interpreter can be resolved on PATH. The boundary coordinates closed local state through RuntimeError with one closed state transition.

    Rationale
    ---------
    Because Raised when no ``python``/``py`` interpreter can be resolved on PATH. Keep RuntimeError inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """


def _resolve_python_interpreter() -> str:
    """Resolve a concrete, absolute path to a python interpreter on PATH.

    Intent
    ------
    Resolve a concrete, absolute path to a python interpreter on PATH. The boundary coordinates resolved through which, WindowsPythonNotFoundError, shutil, resolved, and str with 1 guarded checks, and 1 typed refusals.

    Rationale
    ---------
    Because Resolve a concrete, absolute path to a python interpreter on PATH. Keep which, WindowsPythonNotFoundError, shutil, resolved, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    .WindowsPythonNotFoundError:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Resolve a concrete, absolute path to a python interpreter on PATH."
    """
    resolved = shutil.which("python") or shutil.which("py")
    if not resolved:
        raise WindowsPythonNotFoundError(
            "could not resolve a python interpreter on PATH (tried 'python' and 'py'); "
            "the generated dispatcher.bat requires a concrete, validatable interpreter path"
        )
    return resolved


def _resolver_path(*, home: Path | None = None) -> Path:
    """Return the fixed resolver path beneath this host's runtime_root.

    Intent
    ------
    Return the fixed resolver path beneath this host's runtime_root. The boundary coordinates home, and runtime_root through home, resolve_famulus_paths, joinpath, Path, sys, and runtime_root with one closed state transition.

    Rationale
    ---------
    Because Return the fixed resolver path beneath this host's runtime_root. Keep home, resolve_famulus_paths, joinpath, Path, sys, and runtime_root inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - set runtime_root_selection = received_context
    - return runtime_root_selection

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    officina.common.famulus_paths.resolve_famulus_paths:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Return the fixed resolver path beneath this host's runtime_root."
    """
    home = home or Path.home()
    runtime_root = resolve_famulus_paths(platform=sys.platform, home=home).runtime_root
    return runtime_root.joinpath(*_RESOLVER_RELATIVE_PATH)


def _windows_module_content(module: str, *, home: Path | None = None) -> str:
    """Render one batch shim that delegates a module to the active release.

    Intent
    ------
    Render one batch shim that delegates a module to the active release. The boundary coordinates module, home, resolver, and interpreter through _batch_path, _resolver_path, Path, _resolve_python_interpreter, str, and LauncherInstallerBase with one closed state transition.

    Rationale
    ---------
    Because Render one batch shim that delegates a module to the active release. Keep _batch_path, _resolver_path, Path, _resolve_python_interpreter, str, and LauncherInstallerBase inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    CallsFromRepo
    -------------
    ._resolve_python_interpreter:
      why:
        computes: "This computes edge is the first repository dependency used to uphold this guarantee: Render one batch shim that delegates a module to the active release."
    ._resolver_path:
      why:
        computes: "This computes edge is the second repository dependency used to uphold this guarantee: Render one batch shim that delegates a module to the active release."

    InstantiationsFromRepo
    ----------------------
    ._base_launcher.LauncherInstallerBase._batch_path:
      why:
        constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Render one batch shim that delegates a module to the active release."
    """
    resolver = LauncherInstallerBase._batch_path(_resolver_path(home=home))
    interpreter = LauncherInstallerBase._batch_path(Path(_resolve_python_interpreter()))
    return (
        "@echo off\n"
        "setlocal\n"
        f'"{interpreter}" "{resolver}" -m {module} %*\n'
    )


def _windows_dispatcher_content(repo_root: Path, *, home: Path | None = None) -> str:
    """Preserve the established dispatcher renderer API for external tests.

    Intent
    ------
    Preserve the established dispatcher renderer API for external tests. The boundary coordinates repo_root, and home through _windows_module_content, Path, home, and str with one closed state transition.

    Rationale
    ---------
    Because Preserve the established dispatcher renderer API for external tests. Keep _windows_module_content, Path, home, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none

    InstantiationsFromRepo
    ----------------------
    ._windows_module_content:
      why:
        constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Preserve the established dispatcher renderer API for external tests."
    """
    return _windows_module_content("officina.dispatcher.cli", home=home)


def _windows_invoke_skill_content() -> str:
    """coordinate closed local state through str with one closed state transition.

    Intent
    ------
    coordinate closed local state through str with one closed state transition. The boundary coordinates closed local state through str with one closed state transition.

    Rationale
    ---------
    Because coordinate closed local state through str with one closed state transition. Keep str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """
    return (
        "@echo off\n"
        "setlocal\n"
        "if \"%~1\"==\"\" (\n"
        "  echo Usage: invoke-skill ^<skill-name^> 1>&2\n"
        "  exit /b 2\n"
        ")\n"
        "if not \"%~2\"==\"\" (\n"
        "  echo Usage: invoke-skill ^<skill-name^> 1>&2\n"
        "  exit /b 2\n"
        ")\n"
        "set \"SKILL=%~1\"\n"
        "if \"%ASSISTANT_DEFAULT%\"==\"\" set \"ASSISTANT_DEFAULT=claude\"\n"
        "if /I \"%ASSISTANT_DEFAULT%\"==\"claude\" (\n"
        "  assistant --local --claude --permission-mode bypassPermissions -p \"/%SKILL%\"\n"
        "  exit /b %ERRORLEVEL%\n"
        ")\n"
        "if /I \"%ASSISTANT_DEFAULT%\"==\"codex\" (\n"
        "  set \"CODEX_SKILL=$%SKILL%\"\n"
        "  assistant --local --codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \"%CODEX_SKILL%\"\n"
        "  exit /b %ERRORLEVEL%\n"
        ")\n"
        "echo Unknown ASSISTANT_DEFAULT backend: %ASSISTANT_DEFAULT% 1>&2\n"
        "exit /b 2\n"
    )


class WindowsLauncherInstaller(LauncherInstallerBase):
    """Install launcher bundles on Windows without relying on symlink support.

    Intent
    ------
    Install launcher bundles on Windows without relying on symlink support. The boundary coordinates static_launcher_mode through LauncherInstallerBase with one closed state transition.

    Rationale
    ---------
    Because Install launcher bundles on Windows without relying on symlink support. Keep LauncherInstallerBase inside this boundary so authority or partial state cannot escape before final verification or typed failure.

    Pseudocode
    ----------
    - return

    Wraps
    -----
    - none
    """

    static_launcher_mode = "copy"

    def install_dispatcher_launcher(
        self,
        repo_root: Path,
        bin_dir: Path,
        dry_run: bool,
        *,
        recorder: MutationRecorder | None,
        home: Path | None = None,
    ) -> LauncherInstallResult:
        """Within Install launcher bundles on Windows without relying on symlink support, coordinate repo_root, bin_dir, dry_run, recorder, and home through LauncherBundleSpec, LauncherFileSpec, _windows_dispatcher_content, ins.

        Intent
        ------
        Within Install launcher bundles on Windows without relying on symlink support, coordinate repo_root, bin_dir, dry_run, recorder, and home through LauncherBundleSpec, LauncherFileSpec, _windows_dispatcher_content, ins. The boundary coordinates repo_root, bin_dir, dry_run, recorder, and home through LauncherBundleSpec, LauncherFileSpec, _windows_dispatcher_content, install_bundle, Path, and bool with one closed state transition.

        Rationale
        ---------
        Because Within Install launcher bundles on Windows without relying on symlink support, coordinate repo_root, bin_dir, dry_run, recorder, and home through LauncherBundleSpec, LauncherFileSpec, _windows_dispatcher_content, ins. Keep LauncherBundleSpec, LauncherFileSpec, _windows_dispatcher_content, install_bundle, Path, and bool inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        ._base_launcher.LauncherBundleSpec:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Within Install launcher bundles on Windows without relying on symlink support, coordinate repo_root, bin_dir, dry_run, recorder, and home through LauncherBundleSpec, LauncherFileSpec, _windows_dispatcher_content, ins."
        ._base_launcher.LauncherFileSpec:
          why:
            constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Within Install launcher bundles on Windows without relying on symlink support, coordinate repo_root, bin_dir, dry_run, recorder, and home through LauncherBundleSpec, LauncherFileSpec, _windows_dispatcher_content, ins."
        ._windows_dispatcher_content:
          why:
            constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Within Install launcher bundles on Windows without relying on symlink support, coordinate repo_root, bin_dir, dry_run, recorder, and home through LauncherBundleSpec, LauncherFileSpec, _windows_dispatcher_content, ins."
        """
        bundle = LauncherBundleSpec(
            name="dispatcher",
            workflows=DISPATCHER_WORKFLOWS,
            files=[
                LauncherFileSpec(
                    operation_key="scaffold.launcher.dispatcher",
                    destination=bin_dir / "dispatcher.bat",
                    mode="generate",
                    content=_windows_dispatcher_content(repo_root, home=home),
                )
            ],
        )
        return self.install_bundle(bundle, dry_run=dry_run, recorder=recorder)

    def install_invoke_skill_launcher(
        self,
        bin_dir: Path,
        dry_run: bool,
        *,
        recorder: MutationRecorder | None,
    ) -> LauncherInstallResult:
        """Within Install launcher bundles on Windows without relying on symlink support, coordinate bin_dir, dry_run, recorder, and bundle through LauncherBundleSpec, LauncherFileSpec, _windows_invoke_skill_content, install_bu.

        Intent
        ------
        Within Install launcher bundles on Windows without relying on symlink support, coordinate bin_dir, dry_run, recorder, and bundle through LauncherBundleSpec, LauncherFileSpec, _windows_invoke_skill_content, install_bu. The boundary coordinates bin_dir, dry_run, recorder, and bundle through LauncherBundleSpec, LauncherFileSpec, _windows_invoke_skill_content, install_bundle, Path, and bool with one closed state transition.

        Rationale
        ---------
        Because Within Install launcher bundles on Windows without relying on symlink support, coordinate bin_dir, dry_run, recorder, and bundle through LauncherBundleSpec, LauncherFileSpec, _windows_invoke_skill_content, install_bu. Keep LauncherBundleSpec, LauncherFileSpec, _windows_invoke_skill_content, install_bundle, Path, and bool inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        ._base_launcher.LauncherBundleSpec:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Within Install launcher bundles on Windows without relying on symlink support, coordinate bin_dir, dry_run, recorder, and bundle through LauncherBundleSpec, LauncherFileSpec, _windows_invoke_skill_content, install_bu."
        ._base_launcher.LauncherFileSpec:
          why:
            constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Within Install launcher bundles on Windows without relying on symlink support, coordinate bin_dir, dry_run, recorder, and bundle through LauncherBundleSpec, LauncherFileSpec, _windows_invoke_skill_content, install_bu."
        ._windows_invoke_skill_content:
          why:
            constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Within Install launcher bundles on Windows without relying on symlink support, coordinate bin_dir, dry_run, recorder, and bundle through LauncherBundleSpec, LauncherFileSpec, _windows_invoke_skill_content, install_bu."
        """
        bundle = LauncherBundleSpec(
            name="invoke-skill",
            workflows=INVOKE_SKILL_WORKFLOWS,
            files=[
                LauncherFileSpec(
                    operation_key="scaffold.launcher.invoke-skill",
                    destination=bin_dir / "invoke-skill.bat",
                    mode="generate",
                    content=_windows_invoke_skill_content(),
                )
            ],
        )
        return self.install_bundle(bundle, dry_run=dry_run, recorder=recorder)

    def install_wakeup_launcher(
        self,
        bin_dir: Path,
        dry_run: bool,
        *,
        recorder: MutationRecorder | None,
        home: Path | None = None,
    ) -> LauncherInstallResult:
        """Install both public wakeup names as resolver-backed batch shims.

        Intent
        ------
        Install both public wakeup names as resolver-backed batch shims. The boundary coordinates bin_dir, dry_run, recorder, home, and content through _windows_module_content, LauncherBundleSpec, LauncherFileSpec, install_bundle, Path, and bool with one closed state transition.

        Rationale
        ---------
        Because Install both public wakeup names as resolver-backed batch shims. Keep _windows_module_content, LauncherBundleSpec, LauncherFileSpec, install_bundle, Path, and bool inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        InstantiationsFromRepo
        ----------------------
        ._base_launcher.LauncherBundleSpec:
          why:
            constructs: "This constructs edge is the first repository dependency used to uphold this guarantee: Install both public wakeup names as resolver-backed batch shims."
        ._base_launcher.LauncherFileSpec:
          why:
            constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Install both public wakeup names as resolver-backed batch shims."
        ._windows_module_content:
          why:
            constructs: "This constructs edge is the third repository dependency used to uphold this guarantee: Install both public wakeup names as resolver-backed batch shims."
        """
        content = _windows_module_content("officina.wakeup.cli", home=home)
        bundle = LauncherBundleSpec(
            name="llm-wakeup",
            workflows=WAKEUP_WORKFLOWS,
            files=[
                LauncherFileSpec(
                    operation_key=(
                        "scaffold.launcher.llm-wakeup"
                        if command == "llm-wakeup"
                        else "scaffold.launcher.lw"
                    ),
                    destination=bin_dir / f"{command}.bat",
                    mode="generate",
                    content=content,
                )
                for command in WAKEUP_COMMANDS
            ],
        )
        return self.install_bundle(bundle, dry_run=dry_run, recorder=recorder)

    def install_agent_launcher_files(
        self,
        *,
        source_bin_dir: Path,
        bin_dir: Path,
        agent: str,
        dry_run: bool,
        recorder: MutationRecorder | None,
    ) -> None:
        """Within Install launcher bundles on Windows without relying on symlink support, coordinate source_bin_dir, bin_dir, agent, dry_run, and recorder through log, LauncherBundleSpec, _agent_launcher_files, install_bundle.

        Intent
        ------
        Within Install launcher bundles on Windows without relying on symlink support, coordinate source_bin_dir, bin_dir, agent, dry_run, and recorder through log, LauncherBundleSpec, _agent_launcher_files, install_bundle. The boundary coordinates source_bin_dir, bin_dir, agent, dry_run, and recorder through log, LauncherBundleSpec, _agent_launcher_files, install_bundle, Path, and str with 1 guarded checks.

        Rationale
        ---------
        Because Within Install launcher bundles on Windows without relying on symlink support, coordinate source_bin_dir, bin_dir, agent, dry_run, and recorder through log, LauncherBundleSpec, _agent_launcher_files, install_bundle. Keep log, LauncherBundleSpec, _agent_launcher_files, install_bundle, Path, and str inside this boundary so authority or partial state cannot escape before final verification or typed failure.

        Pseudocode
        ----------
        - return

        Wraps
        -----
        - none

        CallsFromRepo
        -------------
        ._base_launcher.log:
          why:
            computes: "This computes edge is the first repository dependency used to uphold this guarantee: Within Install launcher bundles on Windows without relying on symlink support, coordinate source_bin_dir, bin_dir, agent, dry_run, and recorder through log, LauncherBundleSpec, _agent_launcher_files, install_bundle."

        InstantiationsFromRepo
        ----------------------
        ._base_launcher.LauncherBundleSpec:
          why:
            constructs: "This constructs edge is the second repository dependency used to uphold this guarantee: Within Install launcher bundles on Windows without relying on symlink support, coordinate source_bin_dir, bin_dir, agent, dry_run, and recorder through log, LauncherBundleSpec, _agent_launcher_files, install_bundle."
        """
        if agent == "tw":
            log("  SKIP: tw (tmux not available on Windows)")
            return

        bundle = LauncherBundleSpec(
            name=agent,
            required=False,
            workflows=("agent launcher",),
            files=self._agent_launcher_files(source_bin_dir, bin_dir, agent),
        )
        self.install_bundle(bundle, dry_run=dry_run, recorder=recorder)
