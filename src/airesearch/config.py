"""Resolve explicit inputs and freeze the complete experimental condition."""

import importlib.metadata
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

from jsonschema import Draft202012Validator

from .storage import (
    digest,
    identifier,
    new_directory,
    now,
    read,
    require_positive,
    seal,
    verify,
    write,
)

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
PROMPT_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def expand(value):
    if isinstance(value, str):

        def replace(match):
            name = match[1]
            if name not in os.environ or not os.environ[name]:
                raise ValueError(f"Required configuration variable is unset: {name}")
            return os.environ[name]

        return ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [expand(v) for v in value]
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    return value


def fields(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= set(value)
        or set(value) - set(required) - set(optional)
    ):
        raise ValueError(
            f"Expected required fields {sorted(required)} and optional fields {sorted(optional)}"
        )


def render(template, values):
    if any(
        not re.fullmatch(r"\s*[A-Za-z_][A-Za-z0-9_]*\s*", name)
        for name in re.findall(r"\{\{(.*?)\}\}", template, flags=re.DOTALL)
    ):
        raise ValueError("Malformed prompt placeholder")

    def replace(match):
        if match[1] not in values:
            raise ValueError(f"Unknown prompt variable: {match[1]}")
        value = values[match[1]]
        return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)

    return PROMPT_PATTERN.sub(replace, template)


def code_inventory():
    root = Path(__file__).parent
    return {
        p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in sorted(root.rglob("*.py"))
    }


def load_config(path):
    path = Path(path).resolve()
    value = expand(tomllib.loads(path.read_text()))
    fields(
        value,
        {"id", "workload", "prompt", "response_schema", "harness_profile", "model", "environment"},
        {
            "timeout_seconds",
            "max_parallel",
            "variables",
            "context",
            "harness_options",
            "replicates",
        },
    )
    identifier(value["id"])
    for name in (
        "workload",
        "prompt",
        "response_schema",
        "harness_profile",
        "model",
        "environment",
    ):
        if not isinstance(value[name], str) or not value[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
    for name, default in (("timeout_seconds", 1800), ("max_parallel", 4), ("replicates", 1)):
        value[name] = require_positive(value.get(name, default), name)
    profile_path = path.parent / value["harness_profile"]
    profile = expand(tomllib.loads(profile_path.read_text()))
    fields(profile, {"adapter", "executable"}, {"version", "options"})
    options = {**profile.get("options", {}), **value.get("harness_options", {})}
    profile["options"] = options
    environment_path = path.parent / value["environment"]
    environment = expand(tomllib.loads(environment_path.read_text()))
    fields(environment, {"mode", "inherit_env"}, {"image", "network"})
    if environment["mode"] not in ("local", "docker"):
        raise ValueError("environment.mode must be local or docker")
    if not isinstance(environment["inherit_env"], list) or not all(
        isinstance(n, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n)
        for n in environment["inherit_env"]
    ):
        raise ValueError("inherit_env must list environment variable names")
    if environment["mode"] == "docker":
        if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", environment.get("image", "")):
            raise ValueError("Docker images must be pinned by digest: image@sha256:...")
        if environment.get("network", "bridge") not in ("bridge", "none"):
            raise ValueError("Docker network must be bridge or none")
    from .harnesses import validate_profile

    validate_profile(profile, environment)
    schema = read(path.parent / value["response_schema"])
    Draft202012Validator.check_schema(schema)
    if '"$ref"' in json.dumps(schema):
        raise ValueError("Use a self-contained response schema without $ref")
    context = value.get("context", {})
    variables = value.get("variables", {})
    if not isinstance(context, dict) or not isinstance(variables, dict):
        raise ValueError("context and variables must be tables")
    reserved = {"task_json", "schema_json", "sample_ids", "task_id"}
    if (set(context) | set(variables)) & reserved or set(context) & set(variables):
        raise ValueError("Prompt variable names overlap or use reserved names")
    contexts = {k: (path.parent / v).read_text(encoding="utf-8") for k, v in context.items()}
    return value, profile, environment, schema, contexts


def prepare(config_path, output, replicate=1):
    require_positive(replicate, "replicate")
    config_path = Path(config_path).resolve()
    config, profile, environment, schema, contexts = load_config(config_path)
    workload = (config_path.parent / config["workload"]).resolve()
    workload_sha = verify(workload)
    meta = read(workload / "workload.json")
    template = (config_path.parent / config["prompt"]).read_text(encoding="utf-8")
    settings = {
        "condition": config["id"],
        "model": config["model"],
        "harness": profile,
        "environment": environment,
        "timeout_seconds": config["timeout_seconds"],
        "max_parallel": config["max_parallel"],
        "variables": config.get("variables", {}),
        "attempt_selection": "latest_valid",
    }
    with new_directory(output) as out:
        frozen = out / "frozen"
        shutil.copytree(workload, frozen / "workload")
        (frozen / "workload/bundle.json").unlink()
        write(frozen / "settings.json", settings)
        write(frozen / "schema.json", schema)
        write(frozen / "resolved-config.json", config)
        write(frozen / "context.json", contexts)
        (frozen / "configuration.toml").write_bytes(config_path.read_bytes())
        (frozen / "prompt-template.md").write_text(template, encoding="utf-8")
        for task in meta["tasks"]:
            task_json = read(frozen / "workload/tasks" / task["id"] / "task.json")
            prompt = render(
                template,
                {
                    **settings["variables"],
                    **contexts,
                    "task_json": task_json,
                    "schema_json": schema,
                    "task_id": task["id"],
                    "sample_ids": task["sample_ids"],
                },
            )
            path = frozen / "prompts" / (task["id"] + ".md")
            path.parent.mkdir(exist_ok=True)
            path.write_text(prompt, encoding="utf-8")
        code = code_inventory()
        for name in code:
            target = frozen / "runner/airesearch" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(__file__).parent / name, target)
        (frozen / "runner/entry.py").write_text(
            "from airesearch.cli import main\nraise SystemExit(main())\n"
        )
        repo = Path(__file__).resolve().parents[2]
        for name in ("uv.lock", "pyproject.toml"):
            if (repo / name).is_file():
                shutil.copyfile(repo / name, frozen / name)
        write(
            frozen / "provenance.json",
            {
                "runner_files": code,
                "python": sys.version,
                "jsonschema": importlib.metadata.version("jsonschema"),
                "sample_sha256": meta["sample_sha256"],
                "workload_sha256": workload_sha,
            },
        )
        definition = {
            "run_id": identifier(Path(output).name),
            "condition": config["id"],
            "replicate": replicate,
            "sample_sha256": meta["sample_sha256"],
            "workload_sha256": workload_sha,
            "tasks": meta["tasks"],
            "sample_ids": meta["sample_ids"],
        }
        write(frozen / "run-definition.json", definition)
        frozen_sha = seal(frozen)
        write(
            out / "manifest.json",
            {"format": 1, "prepared_at": now(), "frozen_sha256": frozen_sha, **definition},
        )
    return {"run": str(output), "tasks": len(meta["tasks"]), "frozen_sha256": frozen_sha}


def load_run(run, check_code=False):
    run = Path(run).resolve()
    manifest = read(run / "manifest.json")
    if verify(run / "frozen") != manifest["frozen_sha256"]:
        raise ValueError("Run fingerprint changed")
    workload = read(run / "frozen/workload/workload.json")
    provenance = read(run / "frozen/provenance.json")
    definition = read(run / "frozen/run-definition.json")
    if any(manifest.get(k) != v for k, v in definition.items()):
        raise ValueError("Run manifest disagrees with frozen run definition")
    if (
        manifest["tasks"] != workload["tasks"]
        or manifest["sample_ids"] != workload["sample_ids"]
        or manifest["sample_sha256"] != workload["sample_sha256"]
        or manifest["workload_sha256"] != provenance["workload_sha256"]
    ):
        raise ValueError("Run manifest disagrees with frozen workload")
    if check_code and code_inventory() != read(run / "frozen/provenance.json")["runner_files"]:
        raise ValueError("Runner source changed; use frozen/runner/entry.py or prepare a new run")
    return run, manifest, read(run / "frozen/settings.json")
