"""CLI stages are explicit; only run and sweep --execute launch harness sessions."""

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

from jsonschema.exceptions import SchemaError

from . import data, evaluation
from .config import load_config, prepare
from .runner import preflight, run_tasks, status
from .storage import identifier, verify


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    p = commands.add_parser("fetch", help="Download a checksum-pinned HTTPS JSONL source")
    p.add_argument("--url", required=True)
    p.add_argument("--sha256", required=True)
    p.add_argument("--output", required=True)
    p = commands.add_parser("prepare-data", help="Normalize JSONL and separate private references")
    p.add_argument("--source", required=True)
    p.add_argument("--files-root")
    p.add_argument("--output", required=True)
    p = commands.add_parser("sample")
    p.add_argument("--dataset", required=True)
    p.add_argument("--size", type=int)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", required=True)
    p = commands.add_parser("package")
    p.add_argument("--sample", required=True)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--config", required=True)
    p.add_argument("--replicate", type=int, default=1)
    p.add_argument("--output", required=True)
    for name in ("preflight", "status"):
        p = commands.add_parser(name)
        p.add_argument("--run", required=True)
    p = commands.add_parser("run")
    p.add_argument("--run", required=True)
    p.add_argument("--jobs", type=int)
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--limit", type=int)
    p = commands.add_parser("collect")
    p.add_argument("--run", required=True)
    p.add_argument("--output", required=True)
    p = commands.add_parser("evaluate")
    p.add_argument("--collection", required=True)
    p.add_argument("--evaluator", default="exact_match")
    p.add_argument("--output", required=True)
    p = commands.add_parser("compare")
    p.add_argument("--evaluations", nargs="+", required=True)
    p.add_argument("--output", required=True)
    p = commands.add_parser("verify")
    p.add_argument("bundle")
    p = commands.add_parser("sweep", help="Prepare explicit condition/replicate combinations")
    p.add_argument("--config", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--execute", action="store_true")
    return root


def sweep(config, output, execute=False):
    source = Path(config).resolve()
    spec = tomllib.loads(source.read_text())
    if (
        set(spec) != {"conditions"}
        or not isinstance(spec["conditions"], list)
        or not spec["conditions"]
    ):
        raise ValueError("Sweep requires a nonempty conditions array of experiment-config paths")
    planned, names = [], set()
    for name in spec["conditions"]:
        path = source.parent / name
        condition, *_ = load_config(path)
        for replicate in range(1, condition["replicates"] + 1):
            run_id = identifier(f"{condition['id']}-r{replicate}")
            if run_id in names:
                raise ValueError("Sweep contains duplicate condition/replicate IDs")
            names.add(run_id)
            planned.append((path, Path(output) / run_id, replicate))
    if any(p.exists() for _, p, _ in planned):
        raise ValueError("Sweep destinations must be new; resume prepared runs with run")
    results = [prepare(path, target, replicate) for path, target, replicate in planned]
    # Prepare every condition before any model call. Conditions run sequentially;
    # each run uses its configured bounded workload concurrency.
    if execute:
        for _, target, _ in planned:
            preflight(target)
        for result, (_, target, _) in zip(results, planned):
            result["execution"] = run_tasks(target)
    return {"runs": results}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "fetch":
            result = data.fetch(args.url, args.sha256, args.output)
        elif args.command == "prepare-data":
            result = data.prepare_data(args.source, args.output, args.files_root)
        elif args.command == "sample":
            result = data.sample(args.dataset, args.output, args.size, args.seed)
        elif args.command == "package":
            result = data.package(args.sample, args.output, args.batch_size, args.seed)
        elif args.command == "prepare":
            result = prepare(args.config, args.output, args.replicate)
        elif args.command == "preflight":
            result = preflight(args.run)
        elif args.command == "status":
            result = status(args.run)
        elif args.command == "run":
            result = run_tasks(args.run, args.jobs, args.retry_failed, args.limit)
        elif args.command == "collect":
            result = evaluation.collect(args.run, args.output)
        elif args.command == "evaluate":
            result = evaluation.evaluate(args.collection, args.output, args.evaluator)
        elif args.command == "compare":
            result = evaluation.compare(args.evaluations, args.output)
        elif args.command == "sweep":
            result = sweep(args.config, args.output, args.execute)
        else:
            result = {"sha256": verify(args.bundle)}
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        ImportError,
        AttributeError,
        SchemaError,
        subprocess.SubprocessError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, allow_nan=False))
    if args.command == "run" and not result["complete"]:
        return 2
    if (
        args.command == "sweep"
        and args.execute
        and any(not r["execution"]["complete"] for r in result["runs"])
    ):
        return 2
    return 0
