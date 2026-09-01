"""The shared MCP transport preserves the direct Dispatcher boundary."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from urllib.parse import quote
from urllib.request import urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp_server.py"
CORE = ROOT / "mcp-core.json"
COMPREHENSION_FIXTURE = ROOT / "tests" / "fixtures" / "famulus_comprehension_payloads.json"
# Real stdio cases finish in about 6s sequentially and at most 10.71s in an
# isolated -n8 run. Full-hook worker contention can exceed 15s while the MCP
# server is still progressing, so this remains a bounded capacity allowance,
# not a substitute for detecting a hung session.
REAL_MCP_INTEGRATION_TIMEOUT_SECONDS = 30
# Managed persistence performs initialization plus five serialized MCP calls.
# A focused pair finishes well below this bound, while full-hook contention can
# legitimately exceed the shorter single-probe allowance above.
REAL_MCP_PERSISTENCE_LIFECYCLE_TIMEOUT_SECONDS = 90


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_server(path: Path = SERVER):
    spec = importlib.util.spec_from_file_location("famulus_mcp_server", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server():
    """Load the immutable in-process MCP module once per isolation domain."""
    return _load_server()


def _arguments(server, payload: dict[str, object]):
    argument_type = (
        server.OrderedArguments
        if isinstance(payload.get("options"), list)
        else server.CompactArguments
    )
    return argument_type(**payload)


def _copy_plugin(plugin_root: Path, *, include_graph: bool = False) -> None:
    plugin_root.mkdir(parents=True)
    shutil.copy2(SERVER, plugin_root / SERVER.name)
    shutil.copy2(CORE, plugin_root / CORE.name)
    shutil.copy2(ROOT / "officina.toml", plugin_root / "officina.toml")
    shutil.copy2(ROOT / ".mcp.json", plugin_root / ".mcp.json")
    shutil.copy2(ROOT / "plugin.json", plugin_root / "plugin.json")
    shutil.copy2(ROOT / "mcp.json", plugin_root / "mcp.json")
    shutil.copytree(ROOT / "src", plugin_root / "src")
    shutil.copytree(
        ROOT / "skills",
        plugin_root / "skills",
    )
    shutil.copytree(ROOT / "references", plugin_root / "references")
    shutil.copytree(ROOT / ".claude-plugin", plugin_root / ".claude-plugin")
    shutil.copytree(ROOT / ".codex-plugin", plugin_root / ".codex-plugin")
    if include_graph:
        assert (plugin_root / "skills" / "math-dependency-graph").is_dir()


def _declared_launch(host: str, plugin_root: Path) -> tuple[str, list[str], Path | None]:
    manifest = _json(plugin_root / f".{host}-plugin" / "plugin.json")
    if host == "claude":
        assert manifest["mcpServers"] == {
            "famulus": {
                "command": "python",
                "args": ["${CLAUDE_PLUGIN_ROOT}/mcp_server.py"],
                "env": {
                    "FAMULUS_HOST": "claude",
                    "FAMULUS_PLUGIN_DATA": "${CLAUDE_PLUGIN_DATA}",
                },
            }
        }
        # This is the documented result, not a replacement implementation of
        # Claude's loader.
        return "python", [str(plugin_root / "mcp_server.py")], None

    assert manifest["mcpServers"] == "./.mcp.json"
    servers = _json(plugin_root / manifest["mcpServers"])
    assert set(servers) == {"famulus"}
    declaration = servers["famulus"]
    assert declaration == {
        "command": "python",
        "args": ["mcp_server.py"],
        "cwd": ".",
    }
    return declaration["command"], declaration["args"], plugin_root


def _selected_environment(home: Path) -> dict[str, str]:
    """Expose the already-selected test interpreter through exact `python`."""
    environment = {
        key: value for key, value in os.environ.items() if key != "PYTHONPATH"
    }
    environment["PATH"] = os.pathsep.join(
        (str(Path(sys.executable).parent), environment.get("PATH", ""))
    )
    environment["HOME"] = str(home)
    return environment


def _only_broken_resource_errors(error: BaseException) -> bool:
    import anyio

    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(
            _only_broken_resource_errors(child) for child in error.exceptions
        )
    return isinstance(error, anyio.BrokenResourceError)


@asynccontextmanager
async def _stdio_transport(parameters):
    """Ignore the MCP SDK's Windows-only clean-shutdown send race."""
    from mcp.client.stdio import stdio_client

    try:
        async with stdio_client(parameters) as streams:
            yield streams
    except BaseExceptionGroup as error:
        if not _only_broken_resource_errors(error):
            raise


async def _invoke_through_mcp(host: str, plugin_root: Path, home: Path):
    from mcp import ClientSession, StdioServerParameters

    command, args, cwd = _declared_launch(host, plugin_root)
    environment = _selected_environment(home)
    environment.update(
        {
            "FAMULUS_HOST": host,
            "FAMULUS_PLUGIN_DATA": str(home / "plugin-data"),
        }
    )
    parameters = StdioServerParameters(
        command=command,
        args=args,
        cwd=cwd,
        env=environment,
    )
    result = None
    async with _stdio_transport(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            called = await session.call_tool(
                "invoke",
                arguments={
                    "caller": "milestone-logging",
                    "interface": "common.interface.famulus-paths-get",
                    "version": 1,
                    "arguments": {
                        "positionals": ["logging-path"],
                        "options": {},
                        "stdin": None,
                    },
                },
            )
            unauthorized = await session.call_tool(
                "invoke",
                arguments={
                    "caller": "git-workflow",
                    "interface": "milestone-logging._rtx.interface.record",
                    "version": 1,
                    "arguments": {
                        "positionals": [],
                        "options": {"--path": True},
                        "stdin": None,
                    },
                },
            )
            numeric = await session.call_tool(
                "invoke",
                arguments={
                    "caller": "milestone-logging",
                    "interface": "milestone-logging._rtx.interface.record",
                    "version": 1,
                    "arguments": {
                        "positionals": [],
                        "options": {"--role": 7},
                        "stdin": None,
                    },
                },
            )
            ordered_positionals = await session.call_tool(
                "invoke",
                arguments={
                    "caller": "milestone-logging",
                    "interface": "milestone-logging._rtx.interface.record",
                    "version": 1,
                    "arguments": {
                        "positionals": ["unexpected"],
                        "options": ["--path"],
                        "stdin": None,
                    },
                },
            )
            after_rejections = await session.list_tools()
            result = (
                listed,
                called,
                unauthorized,
                numeric,
                ordered_positionals,
                after_rejections,
            )
    assert result is not None
    return result


def _persistent_launch(host: str, plugin_root: Path, plugin_data: Path):
    if host == "claude":
        declaration = _json(plugin_root / ".claude-plugin" / "plugin.json")[
            "mcpServers"
        ]["famulus"]
        root_token = "${CLAUDE_PLUGIN_ROOT}"
        data_token = "${CLAUDE_PLUGIN_DATA}"
    else:
        declaration = _json(plugin_root / "mcp.json")["mcpServers"]["famulus"]
        root_token = "${PLUGIN_ROOT}"
        data_token = "${PLUGIN_DATA}"
    args = [value.replace(root_token, str(plugin_root)) for value in declaration["args"]]
    environment = {
        name: value.replace(data_token, str(plugin_data))
        for name, value in declaration["env"].items()
    }
    return declaration["command"], args, environment


async def _record_through_persistent_mcp(
    host: str, plugin_root: Path, home: Path, plugin_data: Path, canary: Path
):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command, args, declared = _persistent_launch(host, plugin_root, plugin_data)
    environment = _selected_environment(home)
    environment.update(declared)
    environment["ASSISTANT_LOGS"] = str(canary)
    parameters = StdioServerParameters(command=command, args=args, env=environment)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            record_arguments = {
                "caller": "milestone-logging",
                "interface": "milestone-logging._rtx.interface.record",
                "version": 1,
                "arguments": {
                    "positionals": ["persistent milestone"],
                    "options": {"--role": "task-3-test"},
                    "stdin": None,
                },
            }
            return await session.call_tool("invoke", arguments=record_arguments)


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _terminate_pid(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and _pid_is_alive(pid):
        time.sleep(0.05)
    assert not _pid_is_alive(pid)


def test_plugin_persistence_is_inert_without_plugin_context(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: ordinary MCP startup rewrites direct-run logging state."""
    canary = tmp_path / "inherited-logs"
    monkeypatch.setenv("ASSISTANT_LOGS", str(canary))
    monkeypatch.setenv("XDG_DATA_HOME", "relative-but-irrelevant")
    monkeypatch.delenv("FAMULUS_HOST", raising=False)
    monkeypatch.delenv("FAMULUS_PLUGIN_DATA", raising=False)

    server.configure_plugin_persistence()

    assert os.environ["ASSISTANT_LOGS"] == str(canary)
    assert not canary.exists()


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_plugin_persistence_prepares_logs_without_claiming_manager_ledger(
    server, host: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: startup selects the wrong root, payload, or file mode."""
    plugin_data = tmp_path / host / "plugin data"
    canary = tmp_path / "inherited-canary"
    canary.mkdir()
    marker = canary / "marker.txt"
    marker.write_text("untouched", encoding="utf-8")
    monkeypatch.setenv("FAMULUS_HOST", host)
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))
    monkeypatch.setenv("ASSISTANT_LOGS", str(canary))

    server.configure_plugin_persistence()

    assert os.environ["ASSISTANT_LOGS"] == str(plugin_data / "milestones")
    assert (plugin_data / "milestones").is_dir()
    assert not (plugin_data / "setup").exists()
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert list(canary.iterdir()) == [marker]


def test_plugin_persistence_never_overwrites_existing_manager_ledger(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: MCP startup replaces manager receipts with legacy readiness."""
    plugin_data = tmp_path / "plugin-data"
    status = plugin_data / "setup" / "status.json"
    status.parent.mkdir(parents=True)
    ledger = (
        b'{"active_flow":null,"interfaces":{"root.interface.setup":'
        b'{"required_by":["root.interface.setup"],"version":1}},'
        b'"schema_version":1}\n'
    )
    status.write_bytes(ledger)
    monkeypatch.setenv("FAMULUS_HOST", "codex")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))

    server.configure_plugin_persistence()

    assert status.read_bytes() == ledger


@pytest.mark.parametrize(
    ("host", "data_kind"),
    [
        ("claude", None),
        (None, "absolute"),
        ("unknown", "absolute"),
        ("claude", "empty"),
        ("claude", "relative"),
    ],
)
def test_invalid_plugin_context_fails_before_partial_output(
    server,
    host: str | None,
    data_kind: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Break caught: invalid provenance mutates logs or creates partial state."""
    plugin_data = tmp_path / "must-not-exist"
    canary = tmp_path / "inherited-logs"
    monkeypatch.setenv("ASSISTANT_LOGS", str(canary))
    if host is None:
        monkeypatch.delenv("FAMULUS_HOST", raising=False)
    else:
        monkeypatch.setenv("FAMULUS_HOST", host)
    if data_kind is None:
        monkeypatch.delenv("FAMULUS_PLUGIN_DATA", raising=False)
    else:
        value = {"empty": "", "relative": "relative/path"}.get(
            data_kind, str(plugin_data)
        )
        monkeypatch.setenv("FAMULUS_PLUGIN_DATA", value)

    with pytest.raises(ValueError, match="plugin|FAMULUS_"):
        server.configure_plugin_persistence()

    assert os.environ["ASSISTANT_LOGS"] == str(canary)
    assert not canary.exists()
    assert not plugin_data.exists()


def test_plugin_persistence_rejects_unsafe_log_layout_before_publishing_logs(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Break caught: unsafe startup publishes a writable external log root."""
    plugin_data = tmp_path / "plugin-data"
    outside = tmp_path / "outside"
    plugin_data.mkdir()
    outside.mkdir()
    marker = outside / "marker"
    marker.write_text("untouched", encoding="utf-8")
    try:
        (plugin_data / "milestones").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        # famulus-skip: category=platform-contract; reason=directory symlink creation is unavailable on some hosts; alternate=regular-directory confinement coverage remains
        pytest.skip(f"directory symlinks unavailable: {exc}")
    canary = tmp_path / "inherited-logs"
    monkeypatch.setenv("FAMULUS_HOST", "codex")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(plugin_data))
    monkeypatch.setenv("ASSISTANT_LOGS", str(canary))

    with pytest.raises((OSError, RuntimeError)):
        server.configure_plugin_persistence()

    assert os.environ["ASSISTANT_LOGS"] == str(canary)
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert list(outside.iterdir()) == [marker]


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_real_mcp_persists_status_and_milestone_below_selected_host_root(
    host: str, tmp_path: Path
) -> None:
    """Break caught: stdio children inherit the canary instead of plugin data."""
    plugin = tmp_path / "Plugin Cache" / "famulus"
    plugin_data = tmp_path / "host-data" / host
    canary = tmp_path / "inherited-canary"
    canary.mkdir()
    marker = canary / "marker.txt"
    marker.write_text("untouched", encoding="utf-8")
    _copy_plugin(plugin)

    request = _record_through_persistent_mcp(
        host, plugin, tmp_path / "home", plugin_data, canary
    )
    called = asyncio.run(
        asyncio.wait_for(
            request, timeout=REAL_MCP_PERSISTENCE_LIFECYCLE_TIMEOUT_SECONDS
        )
    )

    result = called.structuredContent["result"]
    assert called.isError is False
    assert result["exit_code"] == 0, result
    assert _json(plugin_data / "setup" / "status.json") == {
        "active_flow": None,
        "interfaces": {},
        "schema_version": 1,
    }
    logs = sorted((plugin_data / "milestones").glob("*/*.jsonl"))
    assert len(logs) == 1
    records = [json.loads(line) for line in logs[0].read_text().splitlines()]
    assert records[0]["role"] == "task-3-test"
    assert records[0]["doing"] == "persistent milestone"
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert list(canary.iterdir()) == [marker]


async def _serve_graph_through_mcp(
    host: str, plugin_root: Path, home: Path, served: Path, port: int
) -> tuple[object, object, object, object, bytes, str, bool]:
    from mcp import ClientSession, StdioServerParameters

    command, args, cwd = _declared_launch(host, plugin_root)
    environment = _selected_environment(home)
    environment.update(
        {
            "FAMULUS_HOST": host,
            "FAMULUS_PLUGIN_DATA": str(home / "plugin-data"),
        }
    )
    parameters = StdioServerParameters(
        command=command,
        args=args,
        cwd=cwd,
        env=environment,
    )
    pid: int | None = None
    completed = False
    result = None
    try:
        async with _stdio_transport(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                called = await session.call_tool(
                    "invoke",
                    arguments={
                        "caller": "math-dependency-graph",
                        "interface": (
                            "math-dependency-graph._rtx.interface.scripts-serve-graph"
                        ),
                        "version": 1,
                        "arguments": {
                            "positionals": [],
                            "options": {
                                "--directory": str(served),
                                "--host": "127.0.0.1",
                                "--port": str(port),
                            },
                            "stdin": None,
                        },
                        "dry_run": False,
                    },
                )
                ready = json.loads(called.structuredContent["result"]["stdout"])
                pid = ready["pid"]
                with urlopen(
                    ready["url"] + quote("known file.txt"), timeout=3.0
                ) as response:
                    body = response.read()
                    cache_control = response.headers["Cache-Control"]
                after = await session.list_tools()
                finite = await session.call_tool(
                    "invoke",
                    arguments={
                        "caller": "milestone-logging",
                        "interface": "milestone-logging._rtx.interface.record",
                        "version": 1,
                        "arguments": {
                            "positionals": [],
                            "options": {"--path": True},
                            "stdin": None,
                        },
                        "dry_run": True,
                    },
                )
                completed = True
                result = (
                    listed,
                    called,
                    after,
                    finite,
                    body,
                    cache_control,
                    _pid_is_alive(pid),
                )
        assert result is not None
        return result
    finally:
        if not completed and pid is not None and _pid_is_alive(pid):
            _terminate_pid(pid)


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_graph_server_returns_through_real_mcp_and_survives(
    host: str, tmp_path: Path, free_tcp_port: int
) -> None:
    """Break caught: the graph child holds MCP pipes or dies with its gateway."""
    plugin = tmp_path / "Plugin Cache" / "famulus"
    served = tmp_path / "served directory with spaces"
    served.mkdir()
    expected = b"real-mcp-task-four"
    (served / "known file.txt").write_bytes(expected)
    _copy_plugin(plugin, include_graph=True)
    pid: int | None = None
    try:
        listed, called, after, finite, body, cache_control, alive = asyncio.run(
            asyncio.wait_for(
                _serve_graph_through_mcp(
                    host, plugin, tmp_path / "home", served, free_tcp_port
                ),
                timeout=REAL_MCP_INTEGRATION_TIMEOUT_SECONDS,
            )
        )
        result = called.structuredContent["result"]
        ready = json.loads(result["stdout"])
        pid = ready["pid"]
        assert [tool.name for tool in listed.tools] == ["invoke"]
        assert called.isError is False
        assert result["exit_code"] == 0
        assert result["stderr"] == ""
        assert ready == {
            "serving": str(served.resolve()),
            "host": "127.0.0.1",
            "port": ready["port"],
            "url": f"http://127.0.0.1:{ready['port']}/",
            "cache": "disabled",
            "pid": pid,
        }
        assert isinstance(ready["port"], int)
        assert isinstance(pid, int) and pid > 0
        assert body == expected
        assert cache_control == "no-store, no-cache, must-revalidate, max-age=0"
        assert alive is True
        assert [tool.name for tool in after.tools] == ["invoke"]
        assert finite.isError is False
        assert finite.structuredContent["result"]["target"] == (
            "milestone-logging._rtx.interface.record"
        )
    finally:
        if pid is not None and _pid_is_alive(pid):
            _terminate_pid(pid)


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_packaged_host_declaration_invokes_dispatcher_through_real_mcp(
    host: str, tmp_path: Path
) -> None:
    """Break caught: a host declaration can list the tool but not invoke Dispatcher."""
    plugin = tmp_path / "Plugin Cache" / "famulus"
    _copy_plugin(plugin)
    contract = _json(plugin / CORE.name)

    listed, called, unauthorized, numeric, ordered_positionals, after = asyncio.run(
        asyncio.wait_for(
            _invoke_through_mcp(host, plugin, tmp_path / "home"),
            timeout=REAL_MCP_INTEGRATION_TIMEOUT_SECONDS,
        )
    )

    assert [tool.name for tool in listed.tools] == [contract["tool"]["name"]]
    tool = listed.tools[0]
    assert tool.description.startswith("Invoke one authorized Famulus interface")
    schema = tool.inputSchema
    assert set(schema["properties"]) == set(contract["tool"]["required"]) | set(
        contract["tool"]["optional"]
    )
    assert schema["required"] == contract["tool"]["required"]
    assert schema["properties"]["dry_run"]["default"] is False
    argument_refs = {
        item["$ref"] for item in schema["properties"]["arguments"]["anyOf"]
    }
    assert argument_refs == {
        "#/$defs/CompactArguments",
        "#/$defs/OrderedArguments",
    }
    definitions = schema["$defs"]
    assert definitions["CompactArguments"]["required"] == [
        "positionals",
        "options",
        "stdin",
    ]
    compact_values = definitions["CompactArguments"]["properties"]["options"][
        "additionalProperties"
    ]["anyOf"]
    assert {value.get("type") for value in compact_values} == {"string", "boolean"}
    assert {value.get("const") for value in compact_values} == {None, True}
    ordered_properties = definitions["OrderedArguments"]["properties"]
    assert ordered_properties["options"]["items"] == {"type": "string"}
    assert ordered_properties["positionals"]["maxItems"] == 0
    assert {item.get("type") for item in definitions["CompactArguments"]["properties"]["stdin"]["anyOf"]} == {
        "string",
        "null",
    }
    output = tool.outputSchema
    assert "#/$defs/ExecutionResult" in {
        item.get("$ref") for item in output["properties"]["result"]["anyOf"]
    }
    assert set(output["$defs"]["ExecutionResult"]["properties"]) == {
        "exit_code",
        "stdout",
        "stderr",
        "dispatcher",
    }
    assert output["$defs"]["ExecutionResult"]["properties"]["dispatcher"][
        "additionalProperties"
    ] is True
    assert called.isError is False
    result = called.structuredContent["result"]
    assert result["exit_code"] == 0, result
    assert Path(result["stdout"].strip()) == (
        tmp_path / "home" / "plugin-data" / "milestones"
    )
    assert result["stderr"] == ""
    assert result["dispatcher"]["target"] == "common.interface.famulus-paths-get"
    assert result["dispatcher"]["warnings"]
    failure = unauthorized.structuredContent["result"]
    assert failure["dispatcher"]["code"] == "dispatcher.unauthorized_caller"
    assert failure["dispatcher"]["interface_id"] == (
        "milestone-logging._rtx.interface.record"
    )
    assert numeric.isError is True
    assert ordered_positionals.isError is True
    assert [tool.name for tool in after.tools] == [contract["tool"]["name"]]


def test_contract_keeps_core_dependency_and_fingerprint_separate_from_skills() -> None:
    contract = _json(CORE)

    assert contract["core_packages"] == [
        "mcp>=1,<2",
        "PyYAML>=6",
        "jsonschema>=4,<5",
    ]
    assert contract["fingerprint"] == [
        "sys.executable",
        "sys.prefix",
        "sys.base_prefix",
        "sys.version_info[:2]",
    ]
    assert all(
        "google" not in package and "keyring" not in package
        for package in contract["core_packages"]
    )


def test_packaged_server_imports_its_own_src_without_pythonpath(tmp_path: Path) -> None:
    plugin = tmp_path / "Plugin Cache" / "famulus"
    _copy_plugin(plugin)
    probe = (
        "import importlib.util; p='mcp_server.py'; "
        "s=importlib.util.spec_from_file_location('x', p); "
        "m=importlib.util.module_from_spec(s); import sys; sys.modules['x']=m; "
        "s.loader.exec_module(m); assert m.invoke"
    )
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=plugin,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_dry_run_matches_direct_dispatcher_resolution(server) -> None:
    from officina.dispatcher.direct_runtime import resolve_dispatch_metadata

    expected = resolve_dispatch_metadata(
        caller_skill="milestone-logging",
        target="milestone-logging._rtx.interface.record",
        target_version=1,
        args=["--path"],
        repository_config=ROOT / "officina.toml",
    ).as_payload()

    assert server.invoke(
        "milestone-logging",
        "milestone-logging._rtx.interface.record",
        1,
        _arguments(server, {"positionals": [], "options": {"--path": True}, "stdin": None}),
        dry_run=True,
    ) == expected


def test_generated_outer_payload_uses_real_tool_field_names(tmp_path: Path) -> None:
    """Break caught: projection omits the required outer interface field."""
    syncer = ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py"
    spec = importlib.util.spec_from_file_location("projection_syncer", syncer)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    graph = module.load_blueprints()["milestone-logging"].repository_graph
    outer = {"caller": "milestone-logging", "interface": "milestone-logging._rtx.interface.record", "version": 1, "arguments": {"positionals": [], "options": {}, "stdin": None}, "dry_run": False}
    generated = module.generated_interface_block("milestone-logging", graph)
    assert all(
        fragment in generated
        for fragment in (
            '"positionals": ["DOING", "PREV"]',
            '"--role": "ROLE"',
            '"--path": true',
            "Omit optional positionals and options that are not needed.",
        )
    )

    async def call():
        from mcp import ClientSession, StdioServerParameters

        plugin = tmp_path / "Plugin Cache" / "famulus"
        _copy_plugin(plugin)
        command, args, cwd = _declared_launch("claude", plugin)
        result = None
        async with _stdio_transport(StdioServerParameters(command=command, args=args, cwd=cwd, env=_selected_environment(tmp_path / "home"))) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tool = (await session.list_tools()).tools[0]
                assert tool.inputSchema["required"] == ["caller", "interface", "version", "arguments"]
                result = await session.call_tool("invoke", arguments={**outer, "arguments": {"positionals": [], "options": {"--path": True}, "stdin": None}, "dry_run": True})
        assert result is not None
        return result

    result = asyncio.run(
        asyncio.wait_for(
            call(), timeout=REAL_MCP_INTEGRATION_TIMEOUT_SECONDS
        )
    )
    assert result.structuredContent["result"]["target"] == outer["interface"]


def test_llm_wakeup_skill_renders_every_public_wakeup_invocation(server) -> None:
    """Break caught: the gateway loses the source dependency for wakeup calls."""
    generated = (ROOT / "skills" / "llm-wakeup" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    expected = {
        "wakeup.interface.auto-policy": (
            "default",
            {
                    "positionals": ["action", "provider", "session-id"],
                "options": {},
                "stdin": None,
            },
            {"positionals": ["on", "claude", "session-id"], "options": {}, "stdin": None},
        ),
        "wakeup.interface.infer-schedule": (
            "default",
            {
                "positionals": [],
                "options": {
                    "--text": "timeout-or-resume-text",
                    "--message": "message",
                    "--delay": "duration",
                },
                "stdin": None,
            },
            {"positionals": [], "options": {}, "stdin": None},
        ),
        "wakeup.interface.explicit-schedule": (
            "default",
            {
                    "positionals": ["provider", "session-id", "reset-time"],
                "options": {"--message": "message", "--delay": "duration"},
                "stdin": None,
            },
            {"positionals": ["claude", "session-id", "1 minute"], "options": {}, "stdin": None},
        ),
        "wakeup.interface.setup": (
            "setup",
            {
                "positionals": ["setup"],
                "options": {
                    "--canonical-python": "FILE",
                    "--plugin-root": "DIR",
                    "--bin-dir": "DIR",
                    "--native-root": "DIR",
                },
                "stdin": None,
            },
            {
                "positionals": ["setup"],
                "options": {
                    "--canonical-python": "/opt/famulus/python",
                    "--plugin-root": "/opt/famulus/plugin",
                    "--bin-dir": "/opt/famulus/bin",
                    "--native-root": "/opt/famulus/native",
                },
                "stdin": None,
            },
        ),
        "wakeup.interface.setup teardown": (
            "teardown",
            {
                "positionals": ["teardown"],
                "options": {"--bin-dir": "DIR", "--native-root": "DIR"},
                "stdin": None,
            },
            {
                "positionals": ["teardown"],
                "options": {
                    "--bin-dir": "/opt/famulus/bin",
                    "--native-root": "/opt/famulus/native",
                },
                "stdin": None,
            },
        ),
    }
    assert "Executable Interfaces:" in generated
    assert "Alternative: `setup`" in generated
    assert "Alternative: `teardown`" in generated
    for interface, (alternative, rendered_arguments, _invocation_arguments) in expected.items():
        interface_id = interface.removesuffix(" teardown")
        assert f"`{interface_id}`" in generated
        assert f"Alternative: `{alternative}`" in generated
        assert json.dumps(rendered_arguments, sort_keys=True) in generated

    FastMCP = pytest.importorskip("mcp.server.fastmcp").FastMCP
    mcp = FastMCP("famulus")
    mcp.tool()(server.invoke)
    assert asyncio.run(mcp.list_tools())[0].name == "invoke"
    concrete = {
        "action": "on",
        "provider": "claude",
        "session-id": "session-id",
        "timeout-or-resume-text": "quota resets in 1 minute",
        "duration": "1 minute",
        "message": "message",
        "reset-time": "1 minute",
        "setup": "setup",
        "teardown": "teardown",
        "FILE": "/opt/famulus/python",
        "DIR": "/opt/famulus",
    }
    for interface, (_alternative, rendered_arguments, _arguments) in expected.items():
        interface_id = interface.removesuffix(" teardown")
        arguments = {
            "positionals": [
                concrete[value] for value in rendered_arguments["positionals"]
            ],
            "options": {
                name: concrete[value]
                for name, value in rendered_arguments["options"].items()
            },
            "stdin": rendered_arguments["stdin"],
        }
        _content, payload = asyncio.run(
            mcp.call_tool(
                "invoke",
                {
                    "caller": "llm-wakeup",
                    "interface": interface_id,
                    "version": 1,
                    "arguments": arguments,
                    "dry_run": True,
                },
            )
        )
        assert payload["result"]["target"] == interface_id


def test_ordered_arguments_match_email_pattern_and_reject_mixed_alternative(server) -> None:
    """Break caught: a projected short-account alternative permits --account too."""
    accepted = server.invoke(
        "email-client",
        "email-client._rtx.interface.mail-attachments",
        1,
        server.OrderedArguments(positionals=(), options=["-a", "account", "42", "43"], stdin=None),
        dry_run=True,
    )
    rejected = server.invoke(
        "email-client",
        "email-client._rtx.interface.mail-attachments",
        1,
        server.OrderedArguments(
            positionals=(),
            options=["-a", "account", "--account", "other", "42"],
            stdin=None,
        ),
        dry_run=True,
    )

    assert accepted["target"] == "email-client._rtx.interface.mail-attachments"
    assert rejected["exit_code"] == 2
    assert rejected["dispatcher"]["code"] == "dispatcher.resolution_failed"

    folders = server.invoke(
        "email-client", "email-client._rtx.interface.mail-folders", 1,
        server.OrderedArguments(positionals=(), options=["--account", "account"], stdin=None),
        dry_run=True,
    )
    assert folders["target"] == "email-client._rtx.interface.mail-folders"


def test_comprehension_fixture_is_an_uncoached_generated_candidate() -> None:
    """Break caught: frozen cases drift or expose the controller oracle."""
    fixture = _json(COMPREHENSION_FIXTURE)

    assert "`famulus` MCP server" in fixture["session_start"]
    assert fixture["mcp_tool"] == "famulus.invoke"
    assert [case["case_id"] for case in fixture["cases"]] == [
        "T3C-A",
        "T3C-B",
        "T3C-C",
    ]
    assert [case["skill"] for case in fixture["cases"]] == [
        "skills/milestone-logging/SKILL.md",
        "skills/milestone-logging/SKILL.md",
        "skills/loose-mode/SKILL.md",
    ]
    assert "Executable Interfaces:" in (
        ROOT / fixture["cases"][0]["skill"]
    ).read_text(encoding="utf-8")
    assert "Executable Interfaces:" in (
        ROOT / fixture["cases"][1]["skill"]
    ).read_text(encoding="utf-8")
    instruction_only = (ROOT / fixture["cases"][2]["skill"]).read_text(
        encoding="utf-8"
    )
    assert "Used Interfaces: none" in instruction_only
    assert "Executable Interfaces:" not in instruction_only
    assert "Executable Interfaces:" not in (
        ROOT / fixture["cases"][2]["skill"]
    ).read_text(encoding="utf-8")
    for case in fixture["cases"]:
        assert set(case) == {"case_id", "skill", "user_task"}


def test_execution_captures_dispatcher_output_without_mcp_stdout(
    server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FAMULUS_HOST", "codex")
    monkeypatch.setenv("FAMULUS_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    server.configure_plugin_persistence()
    result = server.invoke(
        "milestone-logging",
        "common.interface.famulus-paths-get",
        1,
        _arguments(
            server,
            {"positionals": ["logging-path"], "options": {}, "stdin": None},
        ),
    )

    assert result["exit_code"] == 0
    assert result["stdout"].strip() == str(tmp_path / "plugin-data" / "milestones")
    assert result["stderr"] == ""
    assert result["dispatcher"]["target"] == "common.interface.famulus-paths-get"


def test_structured_dispatcher_error_is_returned(server) -> None:
    result = server.invoke(
        "missing-caller",
        "milestone-logging._rtx.interface.record",
        1,
        _arguments(server, {"positionals": [], "options": {"--path": True}, "stdin": None}),
    )

    assert result["exit_code"] == 2
    assert result["dispatcher"]["code"] == "dispatcher.module_not_found"
    assert result["stdout"] == result["stderr"] == ""


@pytest.mark.parametrize(
    ("target", "arguments", "argv"),
    [
        ("milestone-logging._rtx.interface.record", {"positionals": ["one"], "options": {}, "stdin": None}, ["one"]),
        ("milestone-logging._rtx.interface.record", {"positionals": ["one"], "options": {"--role": "task"}, "stdin": None}, ["one", "--role", "task"]),
        ("milestone-logging._rtx.interface.record", {"positionals": [], "options": {"--path": True}, "stdin": None}, ["--path"]),
        ("milestone-logging._rtx.interface.timeline", {"positionals": [], "options": {}, "stdin": None}, []),
    ],
)
def test_json_envelope_matches_direct_dispatcher(
    server, target: str, arguments: dict[str, object], argv: list[str]
) -> None:
    from officina.dispatcher.direct_runtime import resolve_dispatch_metadata

    typed_arguments = _arguments(server, arguments)
    assert server.caller_argv(typed_arguments) == argv
    assert server.invoke(
        "milestone-logging", target, 1, typed_arguments, dry_run=True
    ) == resolve_dispatch_metadata(
        caller_skill="milestone-logging",
        target=target,
        target_version=1,
        args=argv,
        repository_config=ROOT / "officina.toml",
    ).as_payload()


def test_python_prerequisite_has_no_platform_fallback(server) -> None:
    with pytest.raises(RuntimeError, match="python >=3.11"):
        server.require_python((3, 10))


def test_missing_exact_python_path_has_no_declared_fallback(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "Plugin Cache" / "famulus"
    _copy_plugin(plugin)
    command, args, cwd = _declared_launch("codex", plugin)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()

    assert command == "python"
    environment = {"PATH": str(empty_path)}
    assert shutil.which(command, path=environment["PATH"]) is None
    if os.name == "nt":
        # CreateProcess lookup is not redirected by the child environment's
        # PATH, so a launch cannot establish this contract on Windows.
        assert not Path(command).is_absolute()
        return
    with pytest.raises(FileNotFoundError, match="python"):
        subprocess.run(
            [command, *args], cwd=cwd, env=environment, check=False
        )


def test_stdio_transport_ignores_only_a_clean_shutdown_send_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anyio
    import mcp.client.stdio

    @asynccontextmanager
    async def shutdown_race(_parameters):
        yield object(), object()
        raise BaseExceptionGroup("shutdown", [anyio.BrokenResourceError()])

    monkeypatch.setattr(mcp.client.stdio, "stdio_client", shutdown_race)

    async def use_transport() -> None:
        async with _stdio_transport(object()):
            pass

    asyncio.run(use_transport())

    async def nested_teardown_race() -> str:
        result = None
        async with _stdio_transport(object()):
            async with shutdown_race(object()):
                result = "produced value"
        assert result is not None
        return result

    assert asyncio.run(nested_teardown_race()) == "produced value"

    async def fail_in_body() -> None:
        async with _stdio_transport(object()):
            raise ValueError("body failure")

    with pytest.raises(ValueError, match="body failure"):
        asyncio.run(fail_in_body())

    @asynccontextmanager
    async def mixed_teardown_error(_parameters):
        yield object(), object()
        raise BaseExceptionGroup(
            "mixed teardown",
            [anyio.BrokenResourceError(), ValueError("body failure")],
        )

    async def fail_with_mixed_error() -> None:
        async with _stdio_transport(object()):
            async with mixed_teardown_error(object()):
                pass

    with pytest.raises(BaseExceptionGroup, match="mixed teardown") as caught:
        asyncio.run(fail_with_mixed_error())
    assert any(isinstance(error, ValueError) for error in caught.value.exceptions)


def test_invoke_through_mcp_preserves_result_when_session_teardown_breaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anyio
    import mcp
    import mcp.client.stdio

    @asynccontextmanager
    async def clean_transport(_parameters):
        yield object(), object()

    class TeardownRaceSession:
        def __init__(self, _read, _write) -> None:
            self.listed = iter(("listed before", "listed after"))
            self.called = iter(
                ("called", "unauthorized", "numeric", "ordered positionals")
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
            raise BaseExceptionGroup(
                "session shutdown", [anyio.BrokenResourceError()]
            )

        async def initialize(self) -> None:
            return None

        async def list_tools(self):
            return next(self.listed)

        async def call_tool(self, _name, **_kwargs):
            return next(self.called)

    monkeypatch.setattr(mcp.client.stdio, "stdio_client", clean_transport)
    monkeypatch.setattr(mcp, "ClientSession", TeardownRaceSession)
    plugin = tmp_path / "Plugin Cache" / "famulus"
    _copy_plugin(plugin)

    result = asyncio.run(
        _invoke_through_mcp("claude", plugin, tmp_path / "home")
    )

    assert result == (
        "listed before",
        "called",
        "unauthorized",
        "numeric",
        "ordered positionals",
        "listed after",
    )


def test_host_declarations_normalize_to_common_command_contract() -> None:
    contract = _json(CORE)
    claude = _json(ROOT / ".claude-plugin" / "plugin.json")["mcpServers"][
        "famulus"
    ]
    codex = _json(ROOT / "mcp.json")["mcpServers"]["famulus"]

    assert contract["command"] == "python"
    assert contract["args"] == ["mcp_server.py"]
    assert claude["command"] == codex["command"] == contract["command"]
    assert claude["args"] == ["${CLAUDE_PLUGIN_ROOT}/" + contract["args"][0]]
    assert claude["env"] == {
        "FAMULUS_HOST": "claude",
        "FAMULUS_PLUGIN_DATA": "${CLAUDE_PLUGIN_DATA}",
    }
    assert codex["args"] == ["${PLUGIN_ROOT}/" + contract["args"][0]]
    assert codex["type"] == "stdio"
    assert codex["env"] == {
        "FAMULUS_HOST": "codex",
        "FAMULUS_PLUGIN_DATA": "${PLUGIN_DATA}",
    }


def test_packaged_fixture_has_complete_registered_repository_graph(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "plugin"
    _copy_plugin(plugin)
    packaged = {
        path.relative_to(plugin).as_posix()
        for path in (plugin / "skills").rglob("*")
        if path.is_file()
    }

    assert {
        "skills/milestone-logging/blueprint.yaml",
        "skills/setup-interface-manager/_rtx/_setup_manager.py",
        "skills/math-dependency-graph/blueprint.yaml",
    } <= packaged
    assert (
        "skills/setup-interface-manager/_rtx/tests/test_setup_manager.py"
        in packaged
    )


def test_ordered_options_preserve_literal_separator(server) -> None:
    arguments = {"positionals": [], "options": ["--", "--role"], "stdin": None}
    assert server.caller_argv(_arguments(server, arguments)) == ["--", "--role"]


def test_ordered_options_are_lossless_for_repeated_flags(server) -> None:
    from officina.dispatcher.direct_runtime import resolve_dispatch_metadata
    from officina.dispatcher.errors import InvocationError

    argv = [
        "--run", "nightly", "--evidence", "first", "--evidence", "second", "--role", "task"
    ]
    arguments = {"positionals": [], "options": argv, "stdin": None}
    typed_arguments = _arguments(server, arguments)
    assert server.caller_argv(typed_arguments) == argv
    with pytest.raises(InvocationError) as direct:
        resolve_dispatch_metadata(
            caller_skill="milestone-logging",
            target="milestone-logging._rtx.interface.record",
            target_version=1,
            args=argv,
            repository_config=ROOT / "officina.toml",
        )
    result = server.invoke(
        "milestone-logging",
        "milestone-logging._rtx.interface.record",
        1,
        typed_arguments,
        dry_run=True,
    )
    assert result["exit_code"] == 2
    assert result["dispatcher"] == direct.value.as_payload()


def test_compact_options_reject_ambiguous_list_values(server) -> None:
    with pytest.raises(ValueError, match="use ordered options"):
        server.caller_argv(
            server.CompactArguments(
                positionals=[],
                options={"--evidence": ["first", "second"]},
                stdin=None,
            )
        )
