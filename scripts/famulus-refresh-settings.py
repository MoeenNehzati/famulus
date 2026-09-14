#!/usr/bin/env python3
"""Save and restore user preferences around a Famulus plugin reinstall."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import tomllib


PLUGIN = "famulus@nullkit"


def _migrate_codex_dispatcher_tools(value):
    """Carry the former combined-invoke approval to its security tiers."""

    if not isinstance(value, dict):
        return value
    server = value.get("mcp_servers", {}).get("famulus_dispatcher")
    if not isinstance(server, dict) or not isinstance(server.get("tools"), dict):
        return value
    tools = server["tools"]
    legacy = tools.pop("invoke", None)
    approval_mode = legacy.get("approval_mode") if isinstance(legacy, dict) else None
    if isinstance(approval_mode, str):
        for level in range(3):
            tools.setdefault(f"invoke_security_{level}", {"approval_mode": approval_mode})
    return value


async def restore_codex(value):
    # Let Codex edit its own TOML, preserving unrelated settings and comments.
    process = await asyncio.create_subprocess_exec(
        shutil.which("codex") or "codex", "app-server", "--stdio",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async def request(identifier, method, params):
            process.stdin.write((json.dumps({
                "id": identifier, "method": method, "params": params,
            }) + "\n").encode())
            await process.stdin.drain()
            while line := await process.stdout.readline():
                response = json.loads(line)
                if response.get("id") == identifier:
                    if "error" in response:
                        raise RuntimeError(response["error"])
                    return
            raise RuntimeError("Codex closed before restoring plugin settings")

        await request(1, "initialize", {
            "clientInfo": {"name": "famulus-refresh", "version": "1"},
        })
        process.stdin.write(b'{"method":"initialized"}\n')
        await request(2, "config/value/write", {
            "keyPath": f'plugins."{PLUGIN}"',
            "value": value,
            "mergeStrategy": "replace",
        })
    finally:
        if process.returncode is None:
            if sys.platform == "win32":
                # A .cmd launcher owns a child that inherits our pipes.
                cleanup = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(process.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await cleanup.wait()
            else:
                process.kill()
        await process.communicate(b"")


def main():
    action, host, snapshot_name = sys.argv[1:]
    snapshot = Path(snapshot_name)
    directory = Path(os.environ.get(
        "CODEX_HOME" if host == "codex" else "CLAUDE_CONFIG_DIR",
        str(Path.home() / (".codex" if host == "codex" else ".claude")),
    ))
    path = (directory / ("config.toml" if host == "codex" else "settings.json")).resolve()
    parse = tomllib.loads if host == "codex" else json.loads
    if action == "save":
        config = parse(path.read_text(encoding="utf-8")) if path.exists() else {}
        if host == "codex":
            saved = config.get("plugins", {}).get(PLUGIN)
        else:
            saved = {
                key: {PLUGIN: config[key][PLUGIN]}
                for key in ("enabledPlugins", "pluginConfigs")
                if PLUGIN in config.get(key, {})
            }
            if "permissions" in config:
                saved["permissions"] = config["permissions"]
        snapshot.write_text(json.dumps(saved), encoding="utf-8")
        return
    saved = json.loads(snapshot.read_text(encoding="utf-8"))
    if not saved:
        return
    if host == "codex":
        asyncio.run(asyncio.wait_for(restore_codex(_migrate_codex_dispatcher_tools(saved)), timeout=30))
        return
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for key, value in saved.items():
        config.setdefault(key, {}).update(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".famulus-settings-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream, indent=2)
            stream.write("\n")
        os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
