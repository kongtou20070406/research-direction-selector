#!/usr/bin/env python3
"""Build three self-contained host packages from the checkout's Git file index.

This does not install, trust, publish or activate anything. Stage new source files
before building; untracked research data and local configuration are never copied.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess


NAME = "research-direction-selector"
SOURCE = Path(__file__).resolve().parent.parent
# Top-level entries include all indexed descendants, including scripts/assets.
# Runtime presentation resources must be staged just like Python source files.
DIRECTORIES = {"scripts", "references", "docs", "examples", "agents", "formal",
               "native", "benchmark", "tests"}
ROOT_FILES = {"SKILL.md", "LICENSE", "README.md", "README.zh-CN.md", "README.ja-JP.md",
              "CONTRIBUTING.md", "CONTRIBUTING.zh-CN.md", "requirements-formal.txt"}
EXCLUDED = {".rds", ".git", ".lake", "target", "__pycache__", ".venv"}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tracked_files(source):
    result = subprocess.run(["git", "-C", str(source), "ls-files", "--cached", "-z"],
                            check=True, stdout=subprocess.PIPE, timeout=30)
    paths = [Path(p) for p in result.stdout.decode("utf-8").split("\0") if p]
    for path in paths:
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Invalid tracked path")
    return paths


def checked_file(source, relative):
    path = source / relative
    if any(part in EXCLUDED for part in relative.parts):
        raise ValueError("Private/generated input is not packageable: " + str(relative))
    if any(source.joinpath(*relative.parts[:end]).is_symlink() for end in range(1, len(relative.parts) + 1)):
        raise ValueError("Symlinks are not packageable: " + str(relative))
    if not path.is_file() or not path.resolve().is_relative_to(source):
        raise ValueError("Missing or escaping source file: " + str(relative))
    return path


def hook_config(host):
    variable = "CLAUDE_PLUGIN_ROOT" if host == "claude" else "PLUGIN_ROOT"
    # Read the host-provided root in Python, not through shell interpolation.
    # The same command works with POSIX shells, PowerShell and cmd.exe, including
    # spaces and shell metacharacters in the installation path.
    bootstrap = (f"import os,runpy,sys; p=os.path.join(os.environ['{variable}'],"
                 f"'skills','{NAME}','scripts','rds_conversation_hook.py'); "
                 f"sys.path.insert(0,os.path.dirname(p)); sys.argv=[p,'--host','{host}']; runpy.run_path(p,run_name='__main__')")
    handler = {"type": "command", "command": f'python -B -c "{bootstrap}"', "timeout": 3}
    events = {"SessionStart": [{"hooks": [handler]}]}
    if host == "claude":
        events["UserPromptSubmit"] = [{"hooks": [handler]}]
    for event in ["PreToolUse", "PostToolUse"] + (["PostToolUseFailure"] if host == "claude" else []):
        events[event] = [{"matcher": r"^(Bash|bash|PowerShell|exec_command|functions\.exec_command|shell_command)$", "hooks": [handler]}]
    return {"hooks": events}


def build(output, source=SOURCE):
    source = source.resolve()
    output = output.absolute()
    if output.exists():
        raise ValueError("Output must be a new directory; existing output is never overwritten")
    if output.resolve().is_relative_to(source):
        raise ValueError("Choose an output directory outside the checkout")
    paths = tracked_files(source)
    required = {Path("SKILL.md"), Path("scripts/rds_cli.py"),
                Path("scripts/rds_conversation_hook.py"), Path("integrations/omp/rds-status.mjs")}
    if not required.issubset(set(paths)):
        raise ValueError("Required sources are not in the Git index; stage new plugin files first")
    selected = [p for p in paths if not EXCLUDED.intersection(p.parts) and
                (p.parts[0] in DIRECTORIES or p.as_posix() in ROOT_FILES or
                 p.as_posix().startswith(".github/assets/"))]
    sources = [(p, checked_file(source, p)) for p in selected]
    omp_source = checked_file(source, Path("integrations/omp/rds-status.mjs"))
    version_match = re.search(r'^VERSION = "([^"]+)"$', (source / "scripts/rds_cli.py").read_text(encoding="utf-8"), re.M)
    if version_match is None:
        raise ValueError("Cannot read the existing CLI version")
    version = version_match.group(1)
    description = "Evidence-grounded research decisions with a compact factual invocation status"
    output.mkdir(parents=True)
    for host in ("claude", "codex", "omp"):
        root = output / host / NAME
        skill = root / "skills" / NAME
        for relative, original in sources:
            target = skill / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        shutil.copyfile(source / "LICENSE", root / "LICENSE")
        identity = {"name": NAME, "version": version, "description": description,
                    "license": "Apache-2.0"}
        if host == "claude":
            write_json(root / ".claude-plugin/plugin.json", {
                **identity, "author": {"name": "RDS contributors"},
                "hooks": "./integrations/hooks.json"})
            write_json(root / "integrations/hooks.json", hook_config(host))
            write_json(output / host / ".claude-plugin/marketplace.json", {
                "name": "rds-local", "owner": {"name": "RDS contributors"},
                "plugins": [{"name": NAME, "source": "./" + NAME, "description": description}]})
        elif host == "codex":
            # Codex 0.160.0 skips bundled hooks for portable root plugin.json.
            # Its native manifest supports both Skill and hook discovery.
            write_json(root / ".codex-plugin/plugin.json", {
                **identity, "skills": "./skills/", "hooks": "./integrations/hooks.json"})
            write_json(root / "integrations/hooks.json", hook_config(host))
            write_json(output / host / ".agents/plugins/marketplace.json", {
                "name": "rds-local", "interface": {"displayName": "RDS local"},
                "plugins": [{"name": NAME, "source": {"source": "local", "path": "./" + NAME},
                             "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                             "category": "Productivity"}]})
        else:
            write_json(root / "package.json", {**identity, "private": True, "type": "module",
                       "omp": {"extensions": ["./extensions/rds-status.mjs"]}})
            target = root / "extensions/rds-status.mjs"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(omp_source, target)
    return {"output": str(output.resolve()), "version": version, "hosts": ["claude", "codex", "omp"],
            "skill_files_per_host": len(sources), "installed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = build(args.output)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Plugin build failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
