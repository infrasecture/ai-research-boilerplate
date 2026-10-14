# Extending and testing

## Add a dataset

Write a source importer that emits the [JSONL contract](data.md), retaining source
revisions, transformations, exclusions, and license information. Keep its raw
inputs and outputs versioned. Pass that file to `prepare-data`; the rest of the
pipeline is domain-independent. The supplied mock understands only the arithmetic
fixture, so implement another mock if you want deterministic tests for your domain.

## Add a task and evaluator

Create a prompt, optional context fragments, and a self-contained response schema.
Keep `predictions` and `unanswered` as the envelope, but specialize `answer` to
your task (string, object, list, etc.). Validation checks structure and assignment,
not correctness. For generated code, an answer can identify files under the
retained workspace; extend collection/evaluation to run the required checks in
an appropriate execution boundary and retain their outputs.

A simple custom evaluator is an importable Python function:

```python
def case_insensitive(answer, reference):
    return float(str(answer).casefold() == str(reference).casefold())
```

```bash
uv run airesearch evaluate --collection evaluations/collected \
  --evaluator my_evaluators:case_insensitive --output reports/normalized-v1
```

It receives one JSON answer and one JSON reference, and must return a finite score
between zero and one. It is trusted Python code, runs offline on the controller,
and must not invoke an untracked model judge. Source for its module is snapshotted
and hashed. Pin any imported helper modules and data in your project/environment;
automatic transitive dependency capture for arbitrary plugins is not implemented.
For structured diagnostics with many-to-many matches, add a dedicated evaluator
stage rather than forcing a misleading single-answer exact match.

## Add a harness

Extend `OPTION_KEYS`, profile validation, preflight, command construction, response
parsing, and telemetry in `harnesses.py`. Keep the scheduling code provider-neutral.
Do not introduce hidden prompts or provider defaults into the orchestration layer.
Unsupported settings should fail preparation or preflight. Add fixtures for:

- CLI argument translation without shell interpolation.
- Valid, partial, malformed, and failed responses.
- Native usage counters, missing counters, and final-event failures.
- Process cancellation, deadlines, and fresh workspaces on retry.

Preflight verifies flags/version, not every provider configuration or server-side
behavior. Run a small real smoke experiment and inspect raw logs before scaling.
Update documentation with tested CLI versions and limitations.

## Regression checks

```bash
uv sync --locked
bash scripts/test.sh
uv run python scripts/demo.py --output demo-output
```

The suite exercises the full mock pipeline, multiple workers, sampling, file
boundaries, immutability, retries, cancellation, validation, telemetry, Docker
command construction, and comparison compatibility. No test contacts a model.

Freeze new runs after code changes. Do not edit previous frozen runner copies to
make them match new behavior. A source snapshot is intentionally stricter than a
Git revision alone because an experiment can start from an uncommitted working
tree. Keep generated runs in artifact storage with their complete manifests.

## Deliberate scope

The initial engine is single-host and uses a thread pool to supervise independent
CLI subprocesses. It supports one response per session, explicit retries, seeded
uniform sampling, fixed-size packaging, and descriptive comparisons. It does not
provide distributed scheduling, automatic rate-limit retries, model-judge chains,
automatic prompt optimization, cloud artifact upload, or statistical inference.
Add those as explicit stages with their own configuration and retained evidence.
