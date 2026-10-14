"""Build and compare two mock conditions in a new output directory."""

import argparse
from pathlib import Path

from airesearch import data, evaluation
from airesearch.config import prepare
from airesearch.runner import run_tasks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="demo-output")
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    if destination.exists():
        parser.error("Output must be a new directory")
    destination.mkdir(parents=True)
    repo = Path(__file__).resolve().parents[1]
    data.prepare_data(repo / "examples/arithmetic.jsonl", destination / "dataset")
    data.sample(destination / "dataset", destination / "sample", seed=42)
    data.package(destination / "sample", destination / "workload", batch_size=2)
    reports = []
    for condition in ("mock", "mock-baseline"):
        text = (repo / f"configs/experiments/{condition}.toml").read_text()
        replacements = {
            "../../workloads/demo-v1": str(destination / "workload"),
            "../../prompts/": str(repo / "prompts") + "/",
            "../../schemas/": str(repo / "schemas") + "/",
            "../harnesses/": str(repo / "configs/harnesses") + "/",
            "../environments/": str(repo / "configs/environments") + "/",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        config = destination / f"{condition}.toml"
        config.write_text(text)
        run = destination / "runs" / condition
        prepare(config, run)
        result = run_tasks(run)
        if not result["complete"]:
            raise RuntimeError(f"Mock run did not complete: {run}")
        collection = destination / "collections" / condition
        report = destination / "evaluations" / condition
        evaluation.collect(run, collection)
        evaluation.evaluate(collection, report)
        reports.append(report)
    evaluation.compare(reports, destination / "comparison")
    print(f"Open {destination / 'comparison/report.md'}")


if __name__ == "__main__":
    main()
