# AI research experiment template

Use this repository as the starting point for **your own experiment repository**.
It contains the code to prepare inputs, run coding agents in parallel, save their
outputs, and produce reports. Prompts, models, and harness settings are configurable.

**Clone this template, make that copy your experiment, and push experiment work
only to your own repository. Do not push experiment-specific changes or results
back to the template.** Related conditions and repeated runs belong together in
one experiment repository; a separate research project gets a separate copy.

## 1. Make your experiment repository

Replace `TEMPLATE_URL_OR_PATH` with this template's Git URL or local directory:

```bash
git clone TEMPLATE_URL_OR_PATH my-experiment
cd my-experiment
git remote remove origin
```

You now have your own working copy with the template's history, and no configured
push destination. This is the experiment fork. Keep the history so you can identify
which template revision you started from. You do not need to delete `.git` or run
`git init` again.

If you use your hosting service's **Fork** button instead, clone your fork and
check that `git remote -v` points to your fork, not to the original template.
In that case, keep your fork's `origin`.

## 2. Check that it works

Install Python 3.11+ and `uv` on a Linux or macOS host, then run:

```bash
uv sync --locked
uv run --locked python scripts/demo.py
```

Open **`demo-output/comparison/report.md`**.

This runs a complete example using a mock agent. It needs no API key and makes no
paid model calls. It prepares eight arithmetic questions, executes two mock
conditions, and generates tables and a report. The expected scores are 1.0 and
0.25; these check the pipeline, not an AI model's ability.

To repeat it, choose a new directory:

```bash
uv run --locked python scripts/demo.py --output demo-output-2
```

## 3. Set up and run your research

Follow **[Start an experiment](docs/start-an-experiment.md)**. It gives the exact
file edits and commands, in order:

1. Write your research question and prepare your input examples.
2. Choose the prompt, response format, harness, and model.
3. Prepare the inputs and run the agent sessions.
4. Collect the outputs and open the report.

For an ordinary experiment, you mostly edit:

| File or directory | What you put there |
| --- | --- |
| `protocol.md` | Your research question, comparisons, and scoring rules |
| `data/raw/` | Your original examples and reference answers |
| `prompts/` | Instructions for the agent |
| `schemas/` | The format the agent must return |
| `configs/experiments/` | Which data, prompt, model, and harness to use |

You normally do not need to edit the framework code in `src/`.

## 4. Save work to your experiment's repository

If you removed `origin` in step 1, create a new empty repository on your Git host.
Replace `YOUR_EXPERIMENT_REPOSITORY_URL` below with that new repository's URL:

```bash
git remote add origin YOUR_EXPERIMENT_REPOSITORY_URL
git remote -v
```

Check that the destination is **your experiment repository**. Then commit your
experiment definition and push it there:

```bash
git add README.md protocol.md configs prompts schemas
git commit -m "Define my experiment"
git push -u origin HEAD
```

If you cloned a hosting-service fork, its `origin` is already configured; skip
`git remote add`. Update this README to describe your experiment before committing.
Template improvements can be contributed separately. Research-specific prompts,
datasets, runs, and conclusions belong to the experiment repository and its storage.

Generated data, runs, and reports are ignored by Git by default. **A Git push does
not back up those artifacts.** Store their complete directories in your experiment's
artifact storage. If you want to commit a final report, copy it and its supporting
tables into a tracked directory such as `publications/` and add that directory
explicitly. Keep credentials out of Git.

## What the directory names mean

| Name | Meaning |
| --- | --- |
| **Sample** | An example selected for the experiment |
| **Workload** | Inputs assigned to one agent session; start with one example per session |
| **Run** | One execution of the configured experiment across all its workloads |
| **Attempt** | One session execution, including a separately retained retry |
| `scripts/` | Ready-to-run helpers: `demo.py` runs the example; `test.sh` checks the framework |
| `src/airesearch/` | The framework implementation behind the `airesearch` command |
| `runs/` | Saved configurations, prompts, inputs, logs, responses, and attempt status |
| `evaluations/` | Collected data ready for scoring |
| `reports/` | Scores, comparison tables, and written reports |

For example, 20 examples with one example per session produce 20 workloads.
`--jobs 4` runs at most four of those sessions at once. It does not combine them
into a shared conversation.

## Further reference

- [Gathering and preparing data](docs/data.md)
- [Configuration, repeated runs, and retries](docs/experiments.md)
- [Codex, Claude Code, and execution environments](docs/harnesses.md)
- [Scoring and comparisons](docs/evaluation.md)
- [Architecture](docs/architecture.md) and [extending the framework](docs/development.md)

Run `bash scripts/test.sh` to check the framework. Tests use local fixtures and
make no model calls.
