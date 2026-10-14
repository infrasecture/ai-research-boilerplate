# AI research boilerplate

A small, configurable repository for experiments driven by prompts and coding
agent sessions. It provides data ingestion, frozen samples and workloads,
parallel execution, resumable attempts, offline evaluation, and traceable reports.
Included adapters are **Codex**, **Claude Code**, and a deterministic **mock**.

Python 3.11+ and a POSIX host are required. Dependencies are pinned in `uv.lock`.
Real harness CLIs and their credentials are installed separately. The demo needs
neither credentials nor paid model calls.

## Try the complete workflow

```bash
uv sync --locked
uv run --locked python scripts/demo.py
```

Open `demo-output/comparison/report.md`. The script prepares eight arithmetic
examples, runs two mock conditions with four concurrent workloads each, saves
every attempt, and produces per-sample scores and a comparison. The arithmetic
fixture scores 1.0; the deliberately constant-zero baseline scores 0.25. These are
pipeline checks, not AI benchmark results. Use a new `--output` directory to repeat.

## Run the stages yourself

All paths below are relative to this repository. Preparation never invokes a model.

```bash
uv run airesearch prepare-data --source examples/arithmetic.jsonl --output data/prepared/demo-v1
uv run airesearch sample --dataset data/prepared/demo-v1 --seed 42 --output samples/demo-v1
uv run airesearch package --sample samples/demo-v1 --batch-size 2 --output workloads/demo-v1
uv run airesearch prepare --config configs/experiments/mock.toml --output runs/mock-r1
uv run airesearch preflight --run runs/mock-r1
uv run airesearch run --run runs/mock-r1 --jobs 4
uv run airesearch collect --run runs/mock-r1 --output evaluations/mock-r1-collected
uv run airesearch evaluate --collection evaluations/mock-r1-collected --output reports/mock-r1
```

Use `uv run airesearch --help` and subcommand `--help` for all flags.

For a real experiment, set `RESEARCH_MODEL` to an exact model identifier available
to your account, copy and edit an experiment config, and select its harness profile:

```bash
export RESEARCH_MODEL='your-model-identifier'
uv run airesearch prepare --config configs/experiments/codex.toml --output runs/codex-r1
uv run airesearch preflight --run runs/codex-r1
uv run airesearch run --run runs/codex-r1
```

Replace the config with `configs/experiments/claude-code.toml` for Claude Code.
`run` starts real model sessions for those profiles. Models, prompts, schemas,
harness options, environment, concurrency, and timeouts are configuration inputs.
Preflight calls only CLI version/help commands; it does not verify authentication
or model availability through a paid request.

The included local environment separates task directories but **does not prevent
the agent from reading other host files**. For experiments that depend on hiding
reference answers, use the [Docker environment](docs/harnesses.md#docker-execution)
or an equivalently isolated external host. No filesystem isolation claim is made
for a local working directory or a read-only write policy.

## Repository map

| Path | Purpose |
| --- | --- |
| `protocol.md` | Research protocol template and measurement conventions |
| `configs/` | Experiment, harness, environment, and sweep inputs |
| `prompts/`, `schemas/` | Versioned instructions and response contracts |
| `examples/` | Tiny synthetic dataset for the executable demo |
| `data/`, `samples/`, `workloads/` | Gathered data and frozen prepared selections |
| `src/airesearch/` | Preparation, adapters, scheduling, collection, scoring |
| `runs/` | Frozen inputs and append-only execution attempts |
| `evaluations/`, `reports/` | Rebuildable, versioned analysis outputs |
| `scripts/`, `tests/` | Full workflow demonstration and regression checks |

Generated data and runs are ignored by Git. Keep their sealed bundles in durable
artifact storage; Git alone is not their backup. Prompts, configurations, code,
documentation, lockfile, and synthetic fixtures belong in Git. Raw responses and
logs may contain the task data, so publish them according to your dataset terms.

## Documentation

- [Architecture and invariants](docs/architecture.md)
- [Gathering and preparing data](docs/data.md)
- [Configuring experiments and sweeps](docs/experiments.md)
- [Harness adapters and execution environments](docs/harnesses.md)
- [Artifacts, scoring, and comparisons](docs/evaluation.md)
- [Extending and testing the boilerplate](docs/development.md)

## Verify locally

```bash
bash scripts/test.sh
```

Tests use mock processes and recorded event fixtures. They never call a model.
Live Codex/Claude execution and Docker isolation must be smoke-tested on the
experiment host with the intended CLI versions, image, and credentials.
