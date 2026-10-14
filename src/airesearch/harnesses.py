"""Adapter boundary: command construction, preflight, and output normalization."""

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .storage import digest, read

OPTION_KEYS = {
    "mock": {"strategy", "delay_seconds", "fail", "invalid"},
    "codex": {"reasoning_effort", "sandbox", "config"},
    "claude-code": {"effort", "tools", "max_budget_usd"},
}


def validate_profile(profile, environment):
    adapter = profile["adapter"]
    if adapter not in OPTION_KEYS:
        raise ValueError(f"Unknown harness adapter: {adapter}")
    if not isinstance(profile["executable"], str) or not profile["executable"]:
        raise ValueError("executable must be a nonempty string")
    options = profile.get("options", {})
    if not isinstance(options, dict) or set(options) - OPTION_KEYS[adapter]:
        raise ValueError(f"Unsupported {adapter} options; accepted: {sorted(OPTION_KEYS[adapter])}")
    if "version" in profile and not isinstance(profile["version"], str):
        raise ValueError("version must be the exact CLI version string")
    if adapter == "mock":
        if environment["mode"] != "local":
            raise ValueError("The mock adapter runs locally")
        if options.get("strategy", "arithmetic") not in ("arithmetic", "constant_zero"):
            raise ValueError("Unknown mock strategy")
        delay = options.get("delay_seconds", 0)
        if not isinstance(delay, (int, float)) or isinstance(delay, bool) or delay < 0:
            raise ValueError("delay_seconds must be nonnegative")
    if adapter == "codex":
        if options.get("sandbox", "read-only") not in ("read-only", "workspace-write"):
            raise ValueError("Codex sandbox must be read-only or workspace-write")
        if not isinstance(options.get("config", {}), dict):
            raise ValueError("Codex config must be a table of TOML-expression strings")
        if any(not isinstance(v, str) for v in options.get("config", {}).values()):
            raise ValueError("Codex config values must be TOML-expression strings")
        reserved = {"model", "model_reasoning_effort", "sandbox_mode", "approval_policy"}
        if set(options.get("config", {})) & reserved:
            raise ValueError("Use the dedicated model/effort/sandbox fields")
    if adapter == "claude-code":
        if not isinstance(options.get("tools", ""), str):
            raise ValueError("Claude tools must be a comma-separated string")
        if "max_budget_usd" in options and (
            not isinstance(options["max_budget_usd"], (int, float))
            or options["max_budget_usd"] <= 0
        ):
            raise ValueError("max_budget_usd must be positive")


def child_environment(settings):
    names = settings["environment"]["inherit_env"]
    return {name: os.environ[name] for name in names if name in os.environ}


def inspect_runtime(settings):
    profile, environment = settings["harness"], settings["environment"]
    adapter = profile["adapter"]
    if adapter == "mock":
        return {
            "adapter": adapter,
            "version": "mock-1",
            "executable": sys.executable,
            "python": sys.version,
            "isolation": "local workspace only",
        }
    executable = profile["executable"]
    if environment["mode"] == "docker":
        docker = shutil.which("docker")
        if not docker:
            raise ValueError("Docker executable is unavailable")
        prefix = [docker, "run", "--rm", "--network", "none", environment["image"]]
        identity = {
            "adapter": adapter,
            "image": environment["image"],
            "isolation": "container filesystem",
        }
    else:
        executable = shutil.which(executable) or executable
        if not Path(executable).is_file():
            raise ValueError(f"Harness executable unavailable: {executable}")
        executable = str(Path(executable).resolve())
        prefix = []
        identity = {
            "adapter": adapter,
            "executable": executable,
            "executable_sha256": digest(Path(executable).read_bytes()),
            "isolation": "local workspace only",
        }
    probe_env = None if environment["mode"] == "docker" else child_environment(settings)
    version = subprocess.run(
        [*prefix, executable, "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        env=probe_env,
    )
    if version.returncode:
        raise ValueError(f"Harness version check failed: {version.stderr.strip()}")
    identity["version"] = version.stdout.strip()
    if profile.get("version") and profile["version"] != identity["version"]:
        raise ValueError(f"Expected {profile['version']!r}, found {identity['version']!r}")
    help_args = [executable, "exec", "--help"] if adapter == "codex" else [executable, "--help"]
    help_result = subprocess.run(
        [*prefix, *help_args], capture_output=True, text=True, timeout=30, env=probe_env
    )
    required = (
        [
            "--json",
            "--output-schema",
            "--output-last-message",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
        ]
        if adapter == "codex"
        else [
            "--print",
            "--json-schema",
            "--output-format",
            "--no-session-persistence",
            "--safe-mode",
        ]
    )
    if help_result.returncode or any(flag not in help_result.stdout for flag in required):
        raise ValueError("Installed harness does not expose the adapter's required CLI flags")
    return identity


def command(settings, attempt, schema_path):
    """Return argv, controller environment, and optional container name."""
    attempt, schema_path = Path(attempt).resolve(), Path(schema_path).resolve()
    profile, environment = settings["harness"], settings["environment"]
    adapter, options = profile["adapter"], profile["options"]
    workspace = str(attempt / "workspace")
    response = str(attempt / "outputs/native-response.json")
    schema = str(schema_path)
    container_name = None
    prefix = []
    if environment["mode"] == "docker":
        container_name = "airesearch-" + digest(str(attempt).encode())[:20]
        prefix = [
            "docker",
            "run",
            "--rm",
            "--init",
            "--name",
            container_name,
            "--network",
            environment.get("network", "bridge"),
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,src={workspace},dst=/workspace",
            "--mount",
            f"type=bind,src={schema},dst=/contract/schema.json,readonly",
            "--mount",
            f"type=bind,src={attempt / 'outputs'},dst=/outputs",
        ]
        for name in environment["inherit_env"]:
            if name in os.environ:
                prefix += ["--env", name]
        prefix += [environment["image"]]
        workspace, response, schema = (
            "/workspace",
            "/outputs/native-response.json",
            "/contract/schema.json",
        )
    if adapter == "mock":
        argv = [
            sys.executable,
            str(Path(__file__).with_name("mock_worker.py")),
            response,
            json.dumps(options),
        ]
    elif adapter == "codex":
        argv = [
            profile["executable"],
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--skip-git-repo-check",
            "--ephemeral",
            "--json",
            "--model",
            settings["model"],
            "--sandbox",
            options.get("sandbox", "read-only"),
            "-c",
            'approval_policy="never"',
            "-C",
            workspace,
            "--output-schema",
            schema,
            "--output-last-message",
            response,
        ]
        if "reasoning_effort" in options:
            argv += ["-c", "model_reasoning_effort=" + json.dumps(options["reasoning_effort"])]
        for key, value in sorted(options.get("config", {}).items()):
            argv += ["-c", key + "=" + value]
        argv += ["-"]
    else:
        argv = [
            profile["executable"],
            "--print",
            "--safe-mode",
            "--verbose",
            "--output-format",
            "stream-json",
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--disable-slash-commands",
            "--permission-mode",
            "dontAsk",
            "--model",
            settings["model"],
            "--tools",
            options.get("tools", ""),
            "--json-schema",
            json.dumps(read(schema_path), separators=(",", ":")),
        ]
        if "effort" in options:
            argv += ["--effort", str(options["effort"])]
        if "max_budget_usd" in options:
            argv += ["--max-budget-usd", str(options["max_budget_usd"])]
    return prefix + argv, (None if prefix else child_environment(settings)), container_name


def events(path):
    if not Path(path).exists():
        return
    with Path(path).open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value


def telemetry(adapter, path):
    result = {
        name: None
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "total_tokens",
            "reported_cost_usd",
        )
    }
    result["session_id"] = None
    usage = None
    for event in events(path):
        result["session_id"] = event.get("session_id", event.get("thread_id", result["session_id"]))
        if adapter == "claude-code" and event.get("type") == "result":
            usage = event.get("usage")
            result["reported_cost_usd"] = event.get("total_cost_usd")
        elif event.get("type") == "turn.completed":
            usage = event.get("usage")
    if isinstance(usage, dict):
        usage = {k: v if type(v) is int and v >= 0 else None for k, v in usage.items()}
        result["input_tokens"] = usage.get("input_tokens")
        result["output_tokens"] = usage.get("output_tokens")
        result["cached_input_tokens"] = usage.get(
            "cache_read_input_tokens", usage.get("cached_input_tokens")
        )
        result["cache_write_input_tokens"] = usage.get("cache_creation_input_tokens")
        if adapter == "claude-code" and result["input_tokens"] is not None:
            cache = (result["cached_input_tokens"], result["cache_write_input_tokens"])
            result["input_tokens"] = (
                result["input_tokens"] + sum(cache) if all(v is not None for v in cache) else None
            )
        if result["input_tokens"] is not None and result["output_tokens"] is not None:
            result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    cost = result["reported_cost_usd"]
    if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
        result["reported_cost_usd"] = None
    return result


def response(adapter, attempt):
    attempt = Path(attempt)
    if adapter != "claude-code":
        return read(attempt / "outputs/native-response.json")
    final = None
    for event in events(attempt / "events.jsonl"):
        if event.get("type") == "result":
            final = event
    if not final or final.get("is_error"):
        raise ValueError("Claude did not return a successful result event")
    if "structured_output" in final:
        return final["structured_output"]
    return json.loads(final.get("result", ""))
