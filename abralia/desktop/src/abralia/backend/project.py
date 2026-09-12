# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Reusable project enable/inspect/disable operations for CLI and a future UI."""

from __future__ import annotations

from importlib.resources import files
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib

from .ipc import socket_path

BEGIN = "# BEGIN Abralia experiment (managed)\n"
END = "# END Abralia experiment (managed)\n"


def _paths(project):
    root = Path(project).resolve()
    if not root.is_dir():
        raise ValueError("project directory does not exist")
    for directory in (root / ".codex", root / ".agents", root / ".agents/skills", root / ".agents/skills/abralia"):
        if directory.is_symlink():
            raise ValueError(f"refusing project setup through a symlink: {directory}")
    return root, root / ".codex/config.toml", root / ".agents/skills/abralia/SKILL.md", root / ".codex/abralia-project.json"


def _read(path):
    if path.is_symlink():
        raise ValueError(f"refusing to modify symlink: {path}")
    return path.read_text() if path.exists() else ""


def _atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".abralia-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            file.write(content)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def inspect_project(project) -> dict:
    root, config, skill, manifest = _paths(project)
    content = _read(config)
    parsed = tomllib.loads(content)
    state = json.loads(_read(manifest)) if manifest.exists() else None
    return {"project": str(root), "mcp_enabled": "abralia_experiment" in parsed.get("mcp_servers", {}),
            "skill_present": skill.exists(), "managed": state is not None,
            "managed_config_unchanged": bool(state and state["block"] in content),
            "managed_skill_unchanged": bool(state and _read(skill) == state["skill"]),
            "socket": state["socket"] if state else str(socket_path(root))}


def enable_project(project, *, python: str | None = None, endpoint=None, registered_thread_id=None) -> dict:
    root, config, skill, manifest = _paths(project)
    content = _read(config)
    parsed = tomllib.loads(content)
    template = files("abralia").joinpath("resources/skills/abralia/SKILL.md").read_text()
    if manifest.exists():
        state = inspect_project(root)
        if state["managed_config_unchanged"] and state["managed_skill_unchanged"]:
            installed = json.loads(_read(manifest))
            if installed["skill"] != template:
                previous = installed["skill"]
                installed["skill"] = template
                _atomic_write(skill, template)
                try:
                    _atomic_write(manifest, json.dumps(installed, indent=2, ensure_ascii=False) + "\n")
                except OSError:
                    if _read(skill) == template:
                        _atomic_write(skill, previous)
                    raise
                return {"status": "accepted", "reason": "skill_updated", **inspect_project(root)}
            return {"status": "accepted", "reason": "already_enabled", **state}
        raise ValueError("Abralia-managed project files were edited; preserving them for manual reconciliation")
    if "abralia_experiment" in parsed.get("mcp_servers", {}) or BEGIN in content or END in content:
        raise ValueError("existing unmanaged abralia_experiment configuration; refusing to overwrite")
    if skill.exists() and _read(skill) != template:
        raise ValueError("existing user-edited abralia skill; refusing to overwrite")
    endpoint = str(endpoint or socket_path(root))
    # Keep the venv interpreter path; resolving its symlink would escape the venv.
    executable = str(Path(python or sys.executable).absolute())
    args = ["-m", "abralia.backend.mcp", "--project", str(root), "--socket", endpoint]
    if registered_thread_id:
        from uuid import UUID
        args += ["--registered-thread-id", str(UUID(registered_thread_id))]
    block = (BEGIN + "[mcp_servers.abralia_experiment]\n"
             + f"command = {json.dumps(executable, ensure_ascii=False)}\n"
             + f"args = {json.dumps(args, ensure_ascii=False)}\n"
             + f"cwd = {json.dumps(str(root), ensure_ascii=False)}\n"
             + "startup_timeout_sec = 15\ntool_timeout_sec = 15\n" + END)
    separator = "\n" if content and not content.endswith("\n\n") else ""
    installed = content + separator + block
    tomllib.loads(installed)
    state = {"version": 1, "project": str(root), "socket": endpoint, "block": block,
             "separator": separator, "skill": template, "skill_created": not skill.exists(),
             "config_created": not config.exists()}
    try:
        _atomic_write(config, installed)
        if not skill.exists():
            _atomic_write(skill, template)
        _atomic_write(manifest, json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        # Roll back only writes from this enable operation, preserving concurrent edits.
        if config.exists() and _read(config) == installed:
            if state["config_created"]:
                config.unlink()
            else:
                _atomic_write(config, content)
        if state["skill_created"] and skill.exists() and _read(skill) == template:
            skill.unlink()
        raise
    return {"status": "accepted", **inspect_project(root)}


def disable_project(project) -> dict:
    root, config, skill, manifest = _paths(project)
    if not manifest.exists():
        return {"status": "accepted", "reason": "not_managed", **inspect_project(root)}
    state = json.loads(_read(manifest))
    content = _read(config)
    if state["block"] not in content:
        raise ValueError("managed MCP block was edited; preserving project configuration")
    preserved = []
    segment = state["separator"] + state["block"]
    if segment not in content:
        segment = state["block"]
    remaining = content.replace(segment, "", 1)
    tomllib.loads(remaining)
    if not remaining and state["config_created"]:
        config.unlink()
    else:
        _atomic_write(config, remaining)
    if state["skill_created"] and skill.exists():
        if _read(skill) == state["skill"]:
            skill.unlink()
        else:
            preserved.append(str(skill))
    manifest.unlink()
    return {"status": "accepted", "preserved_user_files": preserved, **inspect_project(root)}
