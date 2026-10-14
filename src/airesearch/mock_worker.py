"""A subprocess fixture; intentionally performs no model calls or scoring."""

import json
import sys
import time
from pathlib import Path


def main():
    response_path, options = sys.argv[1], json.loads(sys.argv[2])
    sys.stdin.read()
    print(json.dumps({"type": "thread.started", "thread_id": "mock-session"}), flush=True)
    time.sleep(options.get("delay_seconds", 0))
    if options.get("fail"):
        print("Requested mock execution failure", file=sys.stderr)
        return 3
    task = json.loads(Path("task.json").read_text())
    predictions = []
    for sample in task["samples"]:
        source = sample["input"]
        if options.get("strategy") == "constant_zero":
            answer = 0
        elif source["operation"] == "add":
            answer = source["a"] + source["b"]
        elif source["operation"] == "multiply":
            answer = source["a"] * source["b"]
        else:
            raise ValueError("Mock supports add and multiply demo tasks only")
        predictions.append({"sample_id": sample["id"], "answer": answer})
    response = {
        "predictions": predictions,
        "unanswered": [],
        "notes": "Deterministic mock fixture; not an AI result.",
    }
    if options.get("invalid"):
        response = {"invalid": True}
    Path(response_path).write_text(json.dumps(response))
    # No synthetic token counts: a mock invocation has no model usage.
    print(json.dumps({"type": "turn.completed", "usage": {}}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
