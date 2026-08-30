"""The shared MCP transport preserves the direct Dispatcher boundary."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp_server.py"
CORE = ROOT / "mcp-core.json"
COMPREHENSION_FIXTURE = ROOT / "tests" / "fixtures" / "famulus_comprehension_payloads.json"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_server(path: Path = SERVER):
    spec = importlib.util.spec_from_file_location("famulus_mcp_server", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _arguments(server, payload: dict[str, object]):
    argument_type = (
        server.OrderedArguments
        if isinstance(payload.get("options"), list)
        else server.CompactArguments
    )
    return argument_type(**payload)


def _copy_plugin(plugin_root: Path) -> None:
    plugin_root.mkdir(parents=True)
    shutil.copy2(SERVER, plugin_root / SERVER.name)
    shutil.copy2(CORE, plugin_root / CORE.name)
    shutil.copy2(ROOT / "officina.toml", plugin_root / "officina.toml")
    shutil.copy2(ROOT / ".mcp.json", plugin_root / ".mcp.json")
    shutil.copytree(ROOT / "src", plugin_root / "src")
    registered = ROOT / "skills" / "milestone-logging"
    packaged = plugin_root / "skills" / "milestone-logging"
    for relative in (
        "blueprint.yaml",
        "_rtx/__init__.py",
        "_rtx/blueprint.yaml",
        "_rtx/_milestone_interface.py",
        "_rtx/_milestone_writer.py",
        "_rtx/blueprints/rtx-milestone-writer.yaml",
    ):
        destination = packaged / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(registered / relative, destination)
    for relative in ("blueprint.yaml", "_rtx/blueprint.yaml"):
        source = ROOT / "skills" / "install-assistant-tools" / relative
        destination = plugin_root / "skills" / "install-assistant-tools" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    git_workflow = plugin_root / "skills" / "git-workflow" / "blueprint.yaml"
    git_workflow.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "skills" / "git-workflow" / "blueprint.yaml", git_workflow)
    shutil.copytree(ROOT / ".claude-plugin", plugin_root / ".claude-plugin")
    shutil.copytree(ROOT / ".codex-plugin", plugin_root / ".codex-plugin")


def _declared_launch(host: str, plugin_root: Path) -> tuple[str, list[str], Path | None]:
    manifest = _json(plugin_root / f".{host}-plugin" / "plugin.json")
    if host == "claude":
        assert manifest["mcpServers"] == {
            "famulus": {
                "command": "python",
                "args": ["${CLAUDE_PLUGIN_ROOT}/mcp_server.py"],
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


async def _invoke_through_mcp(host: str, plugin_root: Path, home: Path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command, args, cwd = _declared_launch(host, plugin_root)
    parameters = StdioServerParameters(
        command=command,
        args=args,
        cwd=cwd,
        env=_selected_environment(home),
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            called = await session.call_tool(
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
            return (
                listed,
                called,
                unauthorized,
                numeric,
                ordered_positionals,
                after_rejections,
            )


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
            timeout=15,
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
    assert result["stdout"].endswith(".jsonl\n")
    assert result["stderr"] == ""
    assert result["dispatcher"]["target"] == (
        "milestone-logging._rtx.interface.record"
    )
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


def test_dry_run_matches_direct_dispatcher_resolution() -> None:
    from officina.dispatcher.direct_runtime import resolve_dispatch_metadata

    server = _load_server()
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
        from mcp.client.stdio import stdio_client

        plugin = tmp_path / "Plugin Cache" / "famulus"
        _copy_plugin(plugin)
        command, args, cwd = _declared_launch("claude", plugin)
        async with stdio_client(StdioServerParameters(command=command, args=args, cwd=cwd, env=_selected_environment(tmp_path / "home"))) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tool = (await session.list_tools()).tools[0]
                assert tool.inputSchema["required"] == ["caller", "interface", "version", "arguments"]
                return await session.call_tool("invoke", arguments={**outer, "arguments": {"positionals": [], "options": {"--path": True}, "stdin": None}, "dry_run": True})

    result = asyncio.run(asyncio.wait_for(call(), timeout=15))
    assert result.structuredContent["result"]["target"] == outer["interface"]


def test_ordered_arguments_match_email_pattern_and_reject_mixed_alternative() -> None:
    """Break caught: a projected short-account alternative permits --account too."""
    server = _load_server()
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
    assert "Instruction Interfaces:" in (
        ROOT / fixture["cases"][2]["skill"]
    ).read_text(encoding="utf-8")
    assert "Executable Interfaces:" not in (
        ROOT / fixture["cases"][2]["skill"]
    ).read_text(encoding="utf-8")
    for case in fixture["cases"]:
        assert set(case) == {"case_id", "skill", "user_task"}


def test_execution_captures_dispatcher_output_without_mcp_stdout() -> None:
    server = _load_server()
    result = server.invoke(
        "milestone-logging",
        "milestone-logging._rtx.interface.record",
        1,
        _arguments(server, {"positionals": [], "options": {"--path": True}, "stdin": None}),
    )

    assert result["exit_code"] == 0
    assert result["stdout"].endswith(".jsonl\n")
    assert result["stderr"] == ""
    assert result["dispatcher"]["target"] == "milestone-logging._rtx.interface.record"


def test_structured_dispatcher_error_is_returned() -> None:
    server = _load_server()
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
    target: str, arguments: dict[str, object], argv: list[str]
) -> None:
    from officina.dispatcher.direct_runtime import resolve_dispatch_metadata

    server = _load_server()
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


def test_python_prerequisite_has_no_platform_fallback() -> None:
    server = _load_server()
    with pytest.raises(RuntimeError, match="python >=3.11"):
        server.require_python((3, 10))


def test_missing_exact_python_launch_fails_without_fallback(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "Plugin Cache" / "famulus"
    _copy_plugin(plugin)
    command, args, cwd = _declared_launch("codex", plugin)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()

    assert command == "python"
    with pytest.raises(FileNotFoundError, match="python"):
        subprocess.run(
            [command, *args],
            cwd=cwd,
            env={"PATH": str(empty_path)},
            check=False,
        )


def test_host_declarations_normalize_to_common_command_contract() -> None:
    contract = _json(CORE)
    claude = _json(ROOT / ".claude-plugin" / "plugin.json")["mcpServers"][
        "famulus"
    ]
    codex_manifest = _json(ROOT / ".codex-plugin" / "plugin.json")
    codex = _json(ROOT / codex_manifest["mcpServers"])["famulus"]

    assert contract["command"] == "python"
    assert contract["args"] == ["mcp_server.py"]
    assert claude["command"] == codex["command"] == contract["command"]
    assert claude["args"] == ["${CLAUDE_PLUGIN_ROOT}/" + contract["args"][0]]
    assert codex["args"] == contract["args"]
    assert codex["cwd"] == "."


def test_packaged_fixture_has_only_selected_registered_asset_closure(
    tmp_path: Path,
) -> None:
    plugin = tmp_path / "plugin"
    _copy_plugin(plugin)
    packaged = {
        path.relative_to(plugin).as_posix()
        for path in (plugin / "skills").rglob("*")
        if path.is_file()
    }

    assert packaged == {
        "skills/install-assistant-tools/blueprint.yaml",
        "skills/install-assistant-tools/_rtx/blueprint.yaml",
        "skills/git-workflow/blueprint.yaml",
        "skills/milestone-logging/blueprint.yaml",
        "skills/milestone-logging/_rtx/blueprint.yaml",
        "skills/milestone-logging/_rtx/__init__.py",
        "skills/milestone-logging/_rtx/_milestone_interface.py",
        "skills/milestone-logging/_rtx/_milestone_writer.py",
        "skills/milestone-logging/_rtx/blueprints/rtx-milestone-writer.yaml",
    }


def test_ordered_options_preserve_literal_separator() -> None:
    server = _load_server()
    arguments = {"positionals": [], "options": ["--", "--role"], "stdin": None}
    assert server.caller_argv(_arguments(server, arguments)) == ["--", "--role"]


def test_ordered_options_are_lossless_for_repeated_flags() -> None:
    from officina.dispatcher.direct_runtime import resolve_dispatch_metadata
    from officina.dispatcher.errors import InvocationError

    server = _load_server()
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


def test_compact_options_reject_ambiguous_list_values() -> None:
    server = _load_server()
    with pytest.raises(ValueError, match="use ordered options"):
        server.caller_argv(
            server.CompactArguments(
                positionals=[],
                options={"--evidence": ["first", "second"]},
                stdin=None,
            )
        )
