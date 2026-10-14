# Start an experiment

Work in **your experiment's copy** of the template. If you have not made one yet,
follow [the clone/fork instructions](../README.md#1-make-your-experiment-repository).
Run all commands below from that copy's root directory, after `uv sync --locked`.

The route below uses Codex. To use Claude Code, copy its configuration in step 2
instead. Both follow the same preparation, execution, and reporting commands.

## 1. Write the question and add your examples

Edit `protocol.md`: describe what you want to measure, what you will compare,
and how you will judge the answers.

Create `data/raw/records.jsonl`. Each line is one JSON object:

```json
{"id":"example-001","input":{"operation":"add","a":2,"b":3},"reference":5}
{"id":"example-002","input":{"operation":"multiply","a":4,"b":6},"reference":24}
```

`id` is a unique example name. `input` is what the agent sees. `reference` is the
expected answer, used later for scoring. Omit `reference` if you do not have an
answer; the report will identify that example as unscored.

To start with the working example rather than writing data from scratch:

```bash
mkdir -p data/raw
cp examples/arithmetic.jsonl data/raw/records.jsonl
```

Replace these arithmetic examples with your research data when you are ready.
For code files and other attachments, see [the input format](data.md).

## 2. Copy and edit the experiment files

```bash
cp configs/experiments/codex.toml configs/experiments/my-experiment.toml
cp prompts/arithmetic.md prompts/my-task.md
cp schemas/predictions.schema.json schemas/my-response.schema.json
```

For Claude Code, use `configs/experiments/claude-code.toml` as the source of the
first copy. Install and authenticate your chosen CLI before running the experiment.

Edit `configs/experiments/my-experiment.toml` and set these four fields:

```toml
id = "my-experiment"
workload = "../../workloads/experiment-v1"
prompt = "../../prompts/my-task.md"
response_schema = "../../schemas/my-response.schema.json"
```

Leave the other fields in the copied config in place. Set `max_parallel` to the
maximum number of simultaneous agent sessions and `timeout_seconds` to the
maximum duration of each session. Set the model through the existing variable:

```bash
export RESEARCH_MODEL='your-exact-model-identifier'
```

Edit `prompts/my-task.md` to describe your task. Keep `{{ task_json }}` to include
the examples and `{{ schema_json }}` to show the required response format.
`{{ protocol }}` includes the shared instructions from `prompts/protocol.md`.

Edit `schemas/my-response.schema.json` to describe the answer you expect. The
starter expects numbers. For a classification task you might change `answer` to
a string. Keep the `predictions` and `unanswered` lists: every example must appear
in one of them. Leave the prompt and schema as copied for the arithmetic example.

The default evaluator checks exact JSON equality against `reference`. For tasks
where that is not meaningful, define the scoring procedure before gathering
results; see [custom evaluators](development.md#add-a-task-and-evaluator).

The default environment runs on your host. Separate task folders do not prevent
an agent from reading other host files. If your experiment requires hidden
reference answers, configure [Docker execution](harnesses.md#docker-execution)
or another isolated experiment host before collecting results.

## 3. Prepare the inputs

```bash
uv run --locked airesearch prepare-data --source data/raw/records.jsonl --output data/prepared/experiment-v1
uv run --locked airesearch sample --dataset data/prepared/experiment-v1 --seed 42 --output samples/experiment-v1
uv run --locked airesearch package --sample samples/experiment-v1 --batch-size 1 --output workloads/experiment-v1
```

These commands copy and validate the data, select all examples, and arrange one
example per agent session. A **workload** is the input for one such session.
The `sample` command selects all examples here because no `--size` is supplied.
No model is called during preparation.

## 4. Prepare and execute the run

```bash
uv run --locked airesearch prepare --config configs/experiments/my-experiment.toml --output runs/experiment-r1
uv run --locked airesearch preflight --run runs/experiment-r1
uv run --locked airesearch run --run runs/experiment-r1
```

`prepare` saves the exact configuration, prompt, schema, and input files.
`preflight` checks the installed harness version and required CLI options.
**`run` starts the agent sessions and can incur provider charges.** It uses the
parallelism configured in step 2. Preflight itself makes no model request and
does not confirm account access to the selected model.

For a small first check, run one workload before continuing with the rest:

```bash
uv run --locked airesearch run --run runs/experiment-r1 --limit 1
uv run --locked airesearch run --run runs/experiment-r1
```

Use those two commands instead of the unrestricted `run` command above. The
second invocation skips completed work. Inspect progress at any time with:

```bash
uv run --locked airesearch status --run runs/experiment-r1
```

## 5. Collect the data and read the report

After execution finishes:

```bash
uv run --locked airesearch collect --run runs/experiment-r1 --output evaluations/experiment-r1
uv run --locked airesearch evaluate --collection evaluations/experiment-r1 --output reports/experiment-r1
```

Open **`reports/experiment-r1/report.md`**. The useful outputs are:

| Path | What to look at |
| --- | --- |
| `reports/experiment-r1/report.md` | Scores, coverage, resource totals, and limitations |
| `reports/experiment-r1/scores.csv` | Each example's answer, reference, and score |
| `evaluations/experiment-r1/attempts.csv` | Session status, duration, and reported usage |
| `runs/experiment-r1/tasks/` | Original prompts, responses, logs, and generated files |

Collection and evaluation make no model calls. You can collect partial or failed
runs too; missing work stays visible in the report.

## 6. Change one thing and repeat

Edit the prompt or config, then repeat steps 4 and 5 with new names such as
`runs/experiment-r2`, `evaluations/experiment-r2`, and `reports/experiment-r2`.
Use the same `workloads/experiment-v1` to compare against the same inputs.
For another trial with unchanged settings, add `--replicate 2` to `prepare`.

Compare the reports:

```bash
uv run --locked airesearch compare --evaluations reports/experiment-r1 reports/experiment-r2 --output reports/comparison-v1
```

Open `reports/comparison-v1/report.md`. Existing prepared directories are never
overwritten; choose a new name when rerunning a preparation or reporting command.
Changing a source config does not change a run that was already prepared.

Commit your experiment definition and push it to **your experiment's repository**,
as described in [the README](../README.md#4-save-work-to-your-experiments-repository).
Back up the generated artifacts separately: their directories are ignored by Git.
