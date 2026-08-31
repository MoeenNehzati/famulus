"""
launchers.py — Install per-agent bin launchers, profiles, and worker dirs.

For each agent in --agents (assistant, collab, coauthor, tw): installs its
bin launcher, copies its profile config into Codex/Claude homes, creates its
worker directory, and links its Claude settings file. Durable backend selection
lives in launchers.json; backend environment variables remain process-local
overrides and are never written to shell or registry state.

The copied profile config's `model_instructions_file` is rewritten to an
absolute path pointing at the repo's own agents/<agent>.md, instead of the
relative "agents/<agent>.md" Codex would otherwise resolve against
$CODEX_HOME. This means Codex agent launches work in plugin mode without
needing $CODEX_HOME/agents wired at all (that wiring is dev_link.py's
concern, not a launcher requirement) — confirmed by testing that
model_instructions_file accepts an absolute path.

No agents are preselected: pass --agents explicitly.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Literal, Mapping

InstallMode = Literal["development", "plugin"]

REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if not __package__ and str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import officina.common.toml_io as toml_io
from officina.common.famulus_paths import resolve_famulus_paths
from officina.launchers.agent import ManifestRecorder as Manifest, ensure_launcher_configuration
from officina.runtime.python_machine_interface import PythonArgvMachineInterface
from officina.common.command_files import (
    CommandBundleSpec,
    CommandFileInstaller,
    CommandFileSpec,
    log,
    make_link,
)
_MODEL_INSTRUCTIONS_RE = re.compile(r'^model_instructions_file\s*=\s*".*"$', re.MULTILINE)

ALL_AGENTS = ["assistant", "collab", "coauthor", "tw"]

# tw is a bin-dir alias for tmux-workspace; it has no separate worker dir,
# profile, or durable backend-selection relevance (tmux-workspace isn't an LLM backend).
#
# background_run is the agent the scheduler uses, and it is a separate agent
# precisely so its configuration is separate: an unattended run needs its own
# model and reasoning budget, its own hooks, and its own instructions, none of
# which should be borrowed from whatever the interactive assistant happens to
# be tuned for. Sharing `assistant` meant scheduled jobs silently inherited a
# cheap, low-effort interactive profile.
WORKER_AGENTS = ["assistant", "collab", "coauthor"]


def command_content(canonical_python: Path, plugin_root: Path, agent: str, *, windows: bool) -> str:
    entry = plugin_root / "src" / "officina" / "launchers" / "agent.py"
    argv = (canonical_python, entry, plugin_root, agent)
    if windows:
        return "@echo off\n" + " ".join(f'"{str(item).replace(chr(34), chr(34) * 2)}"' for item in argv) + " %*\n"
    return "#!/bin/sh\nexec " + " ".join(shlex.quote(str(item)) for item in argv) + ' "$@"\n'


def install_agent_launcher_files(
    source_bin_dir: Path,
    bin_dir: Path,
    agent: str,
    dry_run: bool,
    manifest: Manifest | None,
    *,
    canonical_python: Path,
    plugin_root: Path,
) -> None:
    if not dry_run:
        bin_dir.mkdir(parents=True, exist_ok=True)
    if agent == "tw":
        if sys.platform == "win32":
            log("  SKIP: tw (tmux not available on Windows)")
            return
        names = ("tmux-workspace", "tw", "tw-break", "tw-join", "tw-monitor", "tw-help")
        files = [CommandFileSpec(source=source_bin_dir / ("tmux-workspace" if name == "tw" else name), destination=bin_dir / name, mode="link") for name in names]
    else:
        windows = sys.platform == "win32"
        files = [CommandFileSpec(destination=bin_dir / (agent + ".bat" if windows else agent), mode="generate", content=command_content(canonical_python, plugin_root, agent, windows=windows), executable=not windows)]
    CommandFileInstaller().install_bundle(CommandBundleSpec(name=agent, files=files, workflows=("interactive launcher",), required=False), dry_run=dry_run, manifest=manifest)


def worker_root_for_mode(
    mode: InstallMode,
    repo_root: Path,
    home: Path,
    *,
    environ: Mapping[str, str],
) -> Path:
    """Resolve the parent dir workers are created under, by install mode.

    Plugin-mode installs run from an immutable/public plugin-cache checkout,
    so workers (which hold live session data) go under the FamulusPaths
    state dir instead. Development-mode installs run against an explicit
    live repo checkout the user supplied, so `repo_root / "workers"` is
    correct there — it's a live checkout, not a public/immutable tree.
    """
    if mode == "plugin":
        return resolve_famulus_paths(
            platform=sys.platform, home=home, environ=environ
        ).worker_root
    return repo_root / "workers"


def install_worker_dir(
    repo_root: Path,
    agent: str,
    dry_run: bool,
    *,
    mode: InstallMode = "development",
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    if agent not in WORKER_AGENTS:
        return None
    home = home or Path.home()
    wdir = worker_root_for_mode(
        mode, repo_root, home, environ=os.environ if environ is None else environ
    ) / agent
    if dry_run:
        log(f"Would create worker dir {wdir}")
    else:
        wdir.mkdir(parents=True, exist_ok=True)
    return wdir


def write_profile_config_with_absolute_agent_path(
    src_dir: Path,
    dst_dir: Path,
    agent: str,
    agent_md_path: Path,
    dry_run: bool,
    manifest: Manifest | None = None,
) -> None:
    """Copy a profile config to dst, rewriting model_instructions_file.

    Same skip semantics as make_copy: leaves an existing regular file alone
    (machine-local state), replaces a legacy symlink with a real file.
    """
    filename = toml_io.profile_config_filename(agent)
    src = src_dir / filename
    dst = dst_dir / filename
    if not src.exists():
        log(f"  SKIP (missing source): {src}")
        return

    if dst.is_symlink():
        if dry_run:
            log(f"  Would replace legacy symlink with file: {dst}")
        else:
            dst.unlink()
    elif dst.exists():
        log(f"  SKIP (exists, keeping machine-local state): {dst}")
        return

    if dry_run:
        log(f"  Would write (absolute agent path): {dst}")
        return

    with toml_io.open(src_dir, f"{agent}.config.toml", "r") as f:
        content = f.read()
    content = _MODEL_INSTRUCTIONS_RE.sub(
        lambda _match: (
            toml_io.key_value("model_instructions_file", agent_md_path).rstrip("\n")
        ),
        content,
    )
    with toml_io.open(dst_dir, f"{agent}.config.toml", "w") as f:
        f.write(content)
    log(f"  Wrote (absolute agent path): {dst}")
    if manifest is not None:
        manifest.record("file", path=str(dst), purge_only=True)


def install_profile_for_agent(repo_root: Path, profiles_dir: Path, codex_home: Path, claude_home: Path, agent: str, dry_run: bool, manifest: Manifest | None) -> None:
    if agent not in WORKER_AGENTS:
        return
    if not profiles_dir.is_dir():
        log(f"Warning: profiles directory is missing: {profiles_dir}")
        return
    if not dry_run:
        codex_home.mkdir(parents=True, exist_ok=True)
        claude_home.mkdir(parents=True, exist_ok=True)

    config_name = toml_io.profile_config_filename(agent)
    config = profiles_dir / config_name
    agent_md = repo_root / "agents" / f"{agent}.md"
    if config.exists():
        write_profile_config_with_absolute_agent_path(profiles_dir, codex_home, agent, agent_md, dry_run, manifest)
        write_profile_config_with_absolute_agent_path(profiles_dir, claude_home, agent, agent_md, dry_run, manifest)

    settings = profiles_dir / f"{agent}_claude_setting.json"
    if settings.exists():
        make_link(settings, claude_home / settings.name, dry_run, manifest)


def verify_install(bin_dir: Path, agents: list[str]) -> bool:
    """Run --help on each installed agent command and report results.

    Only verifies the agents actually selected (unlike setup_tools.py's old
    fixed VERIFY_CMDS list) — installing a subset shouldn't report FAIL for
    agents that were never asked for.

    On Windows, tmux-workspace is skipped (tmux is not available) and every
    supported agent is verified through its runnable ``.bat`` wrapper.

    Verification remains advisory because installation is intentionally
    non-transactional; callers receive exact failed targets without implying
    that earlier filesystem and profile writes were rolled back.
    """
    log("")
    log("Verifying installation...")
    ok = True
    is_windows = sys.platform == "win32"

    commands: list[str] = []
    for agent in agents:
        commands.extend(
            ["tmux-workspace", "tw", "tw-break", "tw-join", "tw-monitor", "tw-help"]
            if agent == "tw"
            else [agent]
        )
    for name in commands:
        if is_windows and name in {
            "tmux-workspace", "tw", "tw-break", "tw-join", "tw-monitor", "tw-help"
        }:
            log("  SKIP: tw (tmux not available on Windows)")
            continue

        if is_windows:
            dst = bin_dir / f"{name}.bat"
        else:
            dst = bin_dir / name

        if not dst.exists():
            log(f"  FAIL: {dst} not found")
            ok = False
            continue
        if not is_windows and not os.access(dst, os.X_OK):
            log(f"  FAIL: {dst} is not executable")
            ok = False
            continue
        result = subprocess.run([str(dst), "--help"], capture_output=True)
        if result.returncode == 0:
            log(f"  OK:   {dst} --help")
        else:
            log(f"  FAIL: {dst} --help exited {result.returncode}")
            ok = False

    if not ok:
        log("Warning: one or more verification checks failed.")
    return ok


def run(
    *,
    canonical_python: Path,
    repo_root: Path,
    agents: list[str],
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    bin_dir: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    default_llm: str | None = None,
    dry_run: bool = False,
    manifest: Manifest | None = None,
    mode: InstallMode = "development",
) -> bool | None:
    agents = list(dict.fromkeys(agents))
    if invalid := set(agents) - set(ALL_AGENTS):
        raise ValueError(f"Unknown launcher(s): {', '.join(sorted(invalid))}")
    if not agents:
        log("Available launchers: " + ", ".join(ALL_AGENTS))
        return None
    if not canonical_python.is_absolute() or not repo_root.is_absolute():
        raise ValueError("canonical_python and repo_root must be absolute")
    if "tw" in agents:
        try:
            if subprocess.run(["tmux", "-V"], capture_output=True, check=False).returncode:
                return False
        except FileNotFoundError:
            return False
    selected_environ = os.environ if environ is None else environ
    home = home or Path.home()
    paths = resolve_famulus_paths(platform=sys.platform, home=home, environ=selected_environ)
    bin_dir = bin_dir or paths.user_bin
    source_bin_dir = repo_root / "skills" / "install-launchers" / "_rtx/assets/bin"
    profiles_dir = repo_root / "profiles"
    codex_home = codex_home or home / ".codex"
    claude_home = claude_home or home / ".claude"
    if dry_run and any(agent != "tw" for agent in agents):
        log(
            f"  Would ensure durable launcher selection at "
            f"{paths.config_root / 'launchers.json'}"
        )
    elif any(agent != "tw" for agent in agents):
        ensure_launcher_configuration(
            config_root=paths.config_root,
            default_backend=default_llm,
            manifest=manifest,
        )

    for agent in agents:
        install_agent_launcher_files(
            source_bin_dir,
            bin_dir,
            agent,
            dry_run,
            manifest,
            canonical_python=canonical_python,
            plugin_root=repo_root,
        )
        install_worker_dir(
            repo_root,
            agent,
            dry_run,
            mode=mode,
            home=home,
            environ=selected_environ,
        )
        install_profile_for_agent(repo_root, profiles_dir, codex_home, claude_home, agent, dry_run, manifest)

    verified = verify_install(bin_dir, agents) if not dry_run else True

    log("")
    log("Launchers complete.")
    log(f"  Agents installed: {', '.join(agents) if agents else '(none)'}")
    return verified


class Interface(PythonArgvMachineInterface):
    prog = "agent_launchers.py"

    def run(self, argv: list[str]) -> int:
        return main(argv)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--canonical-python", metavar="FILE", required=True)
    parser.add_argument("--plugin-root", metavar="DIR", required=True)
    parser.add_argument("--agents", metavar="LIST", default="",
        help="Comma-separated subset of: assistant,collab,coauthor,tw (default: none)")
    parser.add_argument("--home", metavar="DIR")
    parser.add_argument("--bin-dir", metavar="DIR")
    parser.add_argument("--codex-home", metavar="DIR")
    parser.add_argument("--claude-home", metavar="DIR")
    parser.add_argument("--default-llm", choices=["claude", "codex"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mode", choices=["development", "plugin"], default="development",
        help="development: worker dirs live under --repo-root/workers (live checkout). "
             "plugin: worker dirs live under the platform Famulus state dir (default: development)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    invalid = set(agents) - set(ALL_AGENTS)
    if invalid:
        raise SystemExit(f"Unknown agent(s): {', '.join(sorted(invalid))}. Valid: {', '.join(ALL_AGENTS)}")
    run(
        canonical_python=Path(args.canonical_python),
        repo_root=Path(args.plugin_root),
        agents=agents,
        home=Path(args.home) if args.home else None,
        bin_dir=Path(args.bin_dir) if args.bin_dir else None,
        codex_home=Path(args.codex_home) if args.codex_home else None,
        claude_home=Path(args.claude_home) if args.claude_home else None,
        default_llm=args.default_llm,
        mode=args.mode,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
