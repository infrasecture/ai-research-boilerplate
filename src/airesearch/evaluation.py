"""Offline collection, extensible scoring, and descriptive paired comparisons."""

import importlib
import inspect
import json
import math
from collections import defaultdict
from pathlib import Path

from .config import load_run
from .runner import VALID, attempts, validate_response, verify_attempt
from .storage import (
    canonical,
    csv_file,
    digest,
    jsonl,
    lock,
    new_directory,
    read,
    seal,
    verify,
    write,
)


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def metric_totals(rows, name):
    known = [r[name] for r in rows if r.get(name) is not None]
    return {
        "known_total": sum(known) if known else None,
        "known_measurements": len(known),
        "unknown_measurements": len(rows) - len(known),
    }


def collect(run, output):
    run, manifest, settings = load_run(run)
    with lock(run / ".lock"), new_directory(output) as out:
        reference = read(run / "frozen/workload/private/reference.json")
        schema = read(run / "frozen/schema.json")
        attempt_rows, observations = [], []
        for task in manifest["tasks"]:
            selected = None
            last_status = "pending"
            for path in attempts(run, task["id"]):
                state = verify_attempt(path)
                if state["status"] == "running":
                    raise ValueError(
                        "Run has an unfinished attempt; recover it with run before collecting"
                    )
                last_status = state["status"]
                attempt_rows.append(
                    {
                        "task_id": task["id"],
                        "attempt": int(path.name),
                        "status": state["status"],
                        "duration_seconds": state.get("duration_seconds"),
                        "started_at": state.get("started_at"),
                        "finished_at": state.get("finished_at"),
                        "exit_code": state.get("exit_code"),
                        "error": state.get("error"),
                        "evidence_sha256": digest(canonical(state.get("evidence", {}))),
                        **state.get("usage", {}),
                    }
                )
                if state["status"] in VALID:
                    value = read(path / "response.json")
                    actual = validate_response(value, schema, task["sample_ids"])
                    if actual != state["status"]:
                        raise ValueError(f"Response and retained status disagree: {path}")
                    selected = (path, value)
            answers = (
                {p["sample_id"]: p["answer"] for p in selected[1]["predictions"]}
                if selected
                else {}
            )
            for sample_id in task["sample_ids"]:
                observations.append(
                    {
                        "sample_id": sample_id,
                        "task_id": task["id"],
                        "attempt": int(selected[0].name) if selected else None,
                        "answered": sample_id in answers,
                        "answer": answers.get(sample_id),
                        "status": ("answered" if sample_id in answers else "unanswered")
                        if selected
                        else last_status,
                        "group": reference[sample_id]["group"],
                        "response_sha256": digest((selected[0] / "response.json").read_bytes())
                        if selected
                        else None,
                    }
                )
        duration_rows = [read(p) for p in sorted((run / "executions").glob("*.json"))]
        resources = {
            name: metric_totals(attempt_rows, name)
            for name in (
                "duration_seconds",
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "total_tokens",
                "reported_cost_usd",
            )
        }
        resources["execution_wall_seconds"] = metric_totals(duration_rows, "duration_seconds")
        summary = {
            "format": 1,
            "run": manifest,
            "settings": settings,
            "attempt_selection": "latest_valid",
            "attempts": len(attempt_rows),
            "samples": len(observations),
            "answered": sum(r["answered"] for r in observations),
            "resources": resources,
        }
        write(out / "summary.json", summary)
        write(out / "references.json", reference)
        jsonl(out / "observations.jsonl", observations)
        jsonl(out / "attempts.jsonl", attempt_rows)
        csv_file(
            out / "observations.csv",
            observations,
            [
                "sample_id",
                "task_id",
                "attempt",
                "status",
                "answered",
                "answer",
                "group",
                "response_sha256",
            ],
        )
        csv_file(
            out / "attempts.csv",
            attempt_rows,
            [
                "task_id",
                "attempt",
                "status",
                "duration_seconds",
                "exit_code",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "reported_cost_usd",
                "error",
                "evidence_sha256",
            ],
        )
        fingerprint = seal(out)
    return {
        "output": str(output),
        "sha256": fingerprint,
        "samples": len(observations),
        "attempts": len(attempt_rows),
    }


def exact_match(answer, reference):
    """Strict JSON equality (including number representation and string case)."""
    return float(canonical(answer) == canonical(reference))


def load_evaluator(name):
    if name == "exact_match":
        function = exact_match
    else:
        if ":" not in name:
            raise ValueError("Evaluator must be exact_match or an importable module:function")
        module, function_name = name.split(":", 1)
        function = getattr(importlib.import_module(module), function_name)
    if not callable(function) or not inspect.getsourcefile(function):
        raise ValueError("Evaluator must be a Python function with available source")
    source = Path(inspect.getsourcefile(function)).read_bytes()
    return function, source


def aggregate(rows):
    eligible = [r for r in rows if r["has_reference"]]
    answered = [r for r in eligible if r["answered"]]
    return {
        "samples": len(rows),
        "reference_samples": len(eligible),
        "answered": sum(r["answered"] for r in rows),
        "coverage": sum(r["answered"] for r in rows) / len(rows) if rows else None,
        "end_to_end_score": sum(r["score"] for r in eligible) / len(eligible) if eligible else None,
        "answered_score": sum(r["score"] for r in answered) / len(answered) if answered else None,
        "excluded_without_reference": len(rows) - len(eligible),
    }


def evaluate(collection, output, evaluator="exact_match"):
    collection = Path(collection)
    collection_sha = verify(collection)
    source_summary = read(collection / "summary.json")
    references = read(collection / "references.json")
    function, source = load_evaluator(evaluator)
    policy = {
        "evaluator": evaluator,
        "evaluator_source_sha256": digest(source),
        "pipeline_source_sha256": digest(Path(__file__).read_bytes()),
        "missing_prediction_score": 0,
        "attempt_selection": "latest_valid",
    }
    rows = []
    for observation in read_jsonl(collection / "observations.jsonl"):
        ref = references[observation["sample_id"]]
        score = None
        if ref["has_reference"]:
            score = (
                float(function(observation["answer"], ref["reference"]))
                if observation["answered"]
                else 0.0
            )
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("Evaluator scores must be finite numbers from 0 to 1")
        rows.append(
            {
                **observation,
                "has_reference": ref["has_reference"],
                "reference": ref["reference"],
                "score": score,
            }
        )
    groups = defaultdict(list)
    for row in rows:
        groups[json.dumps(row["group"], sort_keys=True)].append(row)
    group_rows = [
        {"group": group, **aggregate(members)} for group, members in sorted(groups.items())
    ]
    summary = {
        "format": 1,
        "run": source_summary["run"],
        "settings": source_summary["settings"],
        "collection_sha256": collection_sha,
        "evaluator": policy,
        "evaluator_sha256": digest(canonical(policy)),
        "metrics": aggregate(rows),
        "resources": source_summary["resources"],
        "attempts": source_summary["attempts"],
    }
    with new_directory(output) as out:
        write(out / "summary.json", summary)
        (out / "evaluator-source.py").write_bytes(source)
        jsonl(out / "scores.jsonl", rows)
        csv_file(
            out / "scores.csv",
            rows,
            [
                "sample_id",
                "task_id",
                "attempt",
                "group",
                "status",
                "answered",
                "has_reference",
                "answer",
                "reference",
                "score",
                "response_sha256",
            ],
        )
        csv_file(
            out / "groups.csv",
            group_rows,
            [
                "group",
                "samples",
                "reference_samples",
                "answered",
                "coverage",
                "end_to_end_score",
                "answered_score",
            ],
        )
        metrics = summary["metrics"]
        report = f"# Experiment report: {summary['run']['run_id']}\n\n"
        report += f"Condition: `{summary['run']['condition']}`; replicate: {summary['run']['replicate']}.\n\n"
        report += f"Harness: `{summary['settings']['harness']['adapter']}`; model: `{summary['settings']['model']}`.\n\n"
        report += "| Metric | Value |\n| --- | ---: |\n"
        report += "".join(
            f"| {key} | {value if value is not None else 'unknown'} |\n"
            for key, value in metrics.items()
        )
        report += "\nEnd-to-end score assigns zero to every unanswered reference sample, including failures. Answered score is conditional on valid returned predictions. Neither metric proves generalization beyond this sample. Group identifiers retain dependence; these are descriptive statistics without independence-based confidence intervals.\n\n"
        if summary["settings"]["harness"]["adapter"] == "mock":
            report += (
                "**Mock fixture: these are pipeline checks, not AI benchmark measurements.**\n\n"
            )
        report += (
            "## Resource accounting\n\nAll attempts, including failures and retries, contribute. Missing counters remain unknown. Cached tokens are subsets of normalized input tokens. Summed attempt time differs from execution wall time.\n\n```json\n"
            + json.dumps(summary["resources"], indent=2)
            + "\n```\n\n"
        )
        report += f"Collection fingerprint: `{collection_sha}`. Evaluator fingerprint: `{summary['evaluator_sha256']}`.\n\n"
        report += "Inspect [per-sample scores](scores.csv), [group summaries](groups.csv), and [machine-readable provenance](summary.json).\n"
        (out / "report.md").write_text(report, encoding="utf-8")
        fingerprint = seal(out)
    return {"output": str(output), "sha256": fingerprint, **summary["metrics"]}


def compare(evaluations, output):
    if len(evaluations) < 2:
        raise ValueError("Comparison requires at least two evaluations")
    roots = [Path(p) for p in evaluations]
    fingerprints = [verify(p) for p in roots]
    summaries = [read(p / "summary.json") for p in roots]
    for field in ("sample_sha256",):
        if len({s["run"][field] for s in summaries}) != 1:
            raise ValueError("Paired comparisons require the same frozen sample")
    if len({s["evaluator_sha256"] for s in summaries}) != 1:
        raise ValueError("Paired comparisons require the same evaluator policy and code")
    run_ids = [s["run"]["run_id"] for s in summaries]
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("Comparison run IDs must be unique")
    indexed = [{r["sample_id"]: r for r in read_jsonl(p / "scores.jsonl")} for p in roots]
    if any(set(rows) != set(indexed[0]) for rows in indexed[1:]):
        raise ValueError("Sample identities differ")
    paired = []
    for sample_id in sorted(indexed[0]):
        row = {"sample_id": sample_id, "group": indexed[0][sample_id]["group"]}
        for run_id, scores in zip(run_ids, indexed):
            row[f"{run_id}.score"] = scores[sample_id]["score"]
            row[f"{run_id}.status"] = scores[sample_id]["status"]
        paired.append(row)
    conditions = [
        {
            "run_id": s["run"]["run_id"],
            "condition": s["run"]["condition"],
            "replicate": s["run"]["replicate"],
            "harness": s["settings"]["harness"]["adapter"],
            "model": s["settings"]["model"],
            **s["metrics"],
        }
        for s in summaries
    ]
    with new_directory(output) as out:
        csv_file(out / "conditions.csv", conditions, list(conditions[0]))
        csv_file(out / "paired.csv", paired, list(paired[0]))
        write(
            out / "summary.json",
            {
                "evaluations": fingerprints,
                "conditions": conditions,
                "sample_sha256": summaries[0]["run"]["sample_sha256"],
                "evaluator_sha256": summaries[0]["evaluator_sha256"],
            },
        )
        text = "# Descriptive paired comparison\n\n| Run | Coverage | End-to-end score | Answered score |\n| --- | ---: | ---: | ---: |\n"
        text += "".join(
            f"| {c['run_id']} | {c['coverage']} | {c['end_to_end_score']} | {c['answered_score']} |\n"
            for c in conditions
        )
        text += "\nAll rows use the same sample and evaluator. Workload packaging may differ. Inspect changed factors before attributing differences to a model or prompt. Missing results remain visible; no significance or independence claim is made.\n\n[Per-sample pairs](paired.csv) · [Conditions](conditions.csv)\n"
        (out / "report.md").write_text(text, encoding="utf-8")
        seal(out)
    return {"output": str(output), "runs": run_ids}
