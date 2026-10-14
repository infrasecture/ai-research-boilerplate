# Experiment configuration and execution

For the first run, follow [Start an experiment](start-an-experiment.md). This page
is the reference for configuration options and execution behavior. Make changes
in your own experiment fork, with its remote pointing to your repository.

Experiment definitions are TOML. Paths are relative to the TOML file containing
them. Prompt context files are relative to the experiment file. `${NAME}` expands
an environment variable during preparation; an unset value is an error.
Only use expansion for nonsecret experimental settings. Resolved configuration
is retained, so credentials belong exclusively in inherited runtime variables.

Required fields are `id`, `workload`, `prompt`, `response_schema`,
`harness_profile`, `model`, and `environment`. Optional fields are
`timeout_seconds` (1800), `max_parallel` (4), `replicates` (1), `variables`,
`context`, and `harness_options`. Unknown fields fail preparation. The latter
table overrides named options in the selected harness profile.

Harness profiles contain `adapter`, `executable`, optional exact `version`, and
an `options` table. Unsupported option names fail preparation instead of being
silently ignored. Model/harness-specific values are not assumed comparable:
equal effort labels across different providers need not mean equal computation.

## Prompts

Use `{{ name }}` placeholders. The engine supplies `task_json`, `task_id`,
`sample_ids`, and `schema_json`. Add scalar/JSON values through `[variables]`
and external text fragments through `[context]`. Unknown placeholders and
duplicate/reserved variable names fail preparation. No task instruction suffix
is appended by the engine. Preserve these instructions in versioned prompt files.

Every rendered prompt is frozen before execution. The response schema can change
the shape of each prediction's `answer`, but the engine requires the envelope
`predictions: [{sample_id, answer}]` and `unanswered: [sample_id]`. Each assigned
ID must appear exactly once across those lists. The demo also requires `notes`.
Use a self-contained JSON Schema without `$ref`.

## Independent runs and explicit sweeps

```bash
uv run airesearch prepare --config configs/experiments/mock.toml \
  --replicate 2 --output runs/mock-r2
uv run airesearch sweep --config configs/sweep.toml --output runs/sweep-v1
```

A sweep file lists `conditions = ["path/to/condition-a.toml", ...]`. Each
condition's `replicates` determines its run count. A standalone `prepare` creates
one run with the supplied `--replicate` label. All sweep destinations must be new.
Sweeps prepare every run first. Add `--execute` to preflight and execute them.
Conditions execute sequentially; workloads within each condition run in parallel.
This avoids silently multiplying the per-run concurrency limit by condition count.

## Progress, resume, and retry

```bash
uv run airesearch run --run runs/mock-r2 --limit 1
uv run airesearch status --run runs/mock-r2
uv run airesearch run --run runs/mock-r2 --jobs 4
uv run airesearch run --run runs/mock-r2 --retry-failed
```

`--limit` counts newly executed workloads, not samples. Successful and valid
incomplete workloads are skipped on resume. Failed, invalid, timed-out, cancelled,
or recovered interrupted workloads are retried only with `--retry-failed`.
Every retry is a new attempt with fresh workspace inputs. To repeat a successful
or partial analysis, prepare a new run instead of overwriting its observations.

Progress is printed to stderr, with start/finish and 30-second running notices.
The complete native event stream is retained per attempt. The final CLI summary
goes to stdout as JSON. Exit code 0 means success; 1 means a configuration or
processing error; 2 means a run/sweep retains unfinished or unsuccessful workloads.
No-work resumes still report the overall run state.

Timeout is elapsed wall time for the whole invocation. SIGINT/SIGTERM cancels
active process groups and stops queued tasks from starting. After an abrupt host
or orchestrator crash, rerun `run` to recover unfinished states as interrupted.
Unknown interrupted duration remains null. A still-live PID requires inspection
and termination before recovery, to avoid duplicating a surviving process. For
Docker also inspect/remove an orphaned container named in `invocation.json`.

Changing configuration does not affect prepared runs. Use a new run ID for new
prompts, schemas, models, budgets, harnesses, or input data. A `--jobs` override is
recorded in each execution segment; hold it fixed if throughput is an outcome.
