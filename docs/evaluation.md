# Artifacts, scoring, and comparisons

## Retained evidence

`runs/<id>/frozen/` contains the full experimental inputs and runner snapshot.
`runtime.json` holds observed harness identity. `executions/*.json` records each
batch invocation, its concurrency, scheduled tasks, and wall duration.

Each `tasks/<id>/attempts/NNNN/` contains:

| Artifact | Meaning |
| --- | --- |
| `workspace/` | Fresh agent-visible inputs and any in-workspace changes |
| `prompt.md` | Exact instructions sent through stdin |
| `invocation.json` | argv, working directory, timeout, inherited variable names |
| `events.jsonl`, `stderr.log` | Native harness streams |
| `outputs/` | Native response file and other generated artifacts |
| `response.json` | Parsed response before scoring, including schema-invalid JSON |
| `status.json` | Execution outcome, validation state, timing, usage, evidence hashes |

The raw streams remain authoritative for parsing audits. Core evidence hashes
protect the prompt, invocation, streams, normalized response, and final workspace
and output files against unnoticed modification. Symlinks are recorded as link
targets without following them. Inspect and archive these artifacts if your
evaluator scores patches or generated programs.

## Collect without calling a model

```bash
uv run airesearch collect --run runs/condition-r1 --output evaluations/condition-r1-collected
```

The collector verifies frozen inputs and retained evidence, revalidates successful
responses against the run's saved schema, and selects the latest valid attempt
per workload. It exports `observations.csv/jsonl`, `attempts.csv/jsonl`, private
reference records, and a `summary.json` with provenance and resource accounting.
There is one observation for every selected sample, including failed and pending
workloads. An empty or failed response never becomes a successful zero-answer row.

All attempt durations and reported counters contribute to resource accounting,
including failed attempts and retries. Each total includes the number of known
and unknown measurements. Execution wall time sums batch invocation durations;
it excludes gaps between resumptions. Attempt time sums concurrent durations.
Neither is inferred from token counts. Provider-reported cost is retained when
available; no price table or estimated billing is built into the engine.

Codex normalization uses the last reported `turn.completed.usage` snapshot in the
invocation; the raw stream is retained to audit that assumption for your CLI.
Cached input is a subset of input and is not added again. Claude's final-result
input counter excludes its cache-read/cache-creation counters, so normalization
adds those into total input once. Missing counters remain null. Missing final
events can leave interrupted consumption unknown.

## Score and compare

```bash
uv run airesearch evaluate --collection evaluations/condition-r1-collected \
  --evaluator exact_match --output reports/condition-r1
uv run airesearch compare --evaluations reports/condition-r1 reports/other-r1 \
  --output reports/comparison-v1
```

The built-in evaluator uses exact canonical JSON equality, including numeric
representation (`1` versus `1.0`), string case, and array order. JSON object key
order is ignored. Change the answer schema/evaluator for the domain under study.

End-to-end score uses all reference-bearing selected samples, with zero for
missing predictions. Answered score is conditional on valid predictions. Coverage
is answered samples divided by all selected samples. Missing references are
excluded explicitly, and a zero denominator yields null. Group tables preserve
correlated identities without treating them as independent repetitions.

Evaluations retain their own fingerprint, evaluator source and policy, collection
fingerprint, per-sample scores, group summaries, and a Markdown report. Re-scoring
creates a new directory and never modifies the original run. Custom Python
evaluators are described in [development.md](development.md).

Paired comparisons require identical sample and evaluator fingerprints and unique
run IDs. Workload fingerprints may differ for batching experiments. The output
includes every sample's score and status in every condition, plus condition-level
tables. Missing work is retained in end-to-end comparisons rather than discarded.
The report does not claim statistical significance or causal attribution when
multiple experimental factors differ.

For open-ended findings, replace exact matching with a versioned correspondence
and adjudication procedure. Preserve raw predictions, unresolved candidates,
reviewer identity, decisions, and the reference population. Do not tune labels
against one condition and silently reuse its score as an independent comparison.
