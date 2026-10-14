# Architecture

The engine is a sequence of inspectable filesystem transformations:

```text
source JSONL + files
    -> prepared dataset (public inputs / private references)
    -> selected sample
    -> packaged workloads
    -> frozen run (condition + replicate)
    -> independent attempt processes
    -> collected observations and usage
    -> evaluation
    -> comparison and report
```

A sample is scored; a workload is executed. One workload can contain several
samples. A condition fixes the prompt, model/harness combination, environment,
schema, and budget. A run applies that condition to a workload collection once.
An attempt is one process invocation for one workload. An evaluation applies a
versioned scoring procedure to retained responses.

## Modules

| Module | Responsibility |
| --- | --- |
| `data.py` | Fetch, ingest, sample, and package |
| `config.py` | Validate settings, resolve paths/variables, freeze runs |
| `harnesses.py` | CLI compatibility, command construction, native output parsing |
| `runner.py` | Worker pool, locks, attempts, deadlines, cancellation, recovery |
| `evaluation.py` | Collection, scoring, resource summaries, paired comparisons |
| `storage.py` | Atomic JSON writes, new destinations, bundle hashes, safe paths |
| `cli.py` | Thin command-line interface and condition sweeps |

The default installation uses only one external runtime dependency, `jsonschema`.
The CLI's control plane runs on a POSIX host; workers may execute locally or in
Docker. This is a single-host orchestrator, with one lock per run. It is not a
distributed queue or a cross-host concurrency manager.

## Immutable and mutable state

Data, samples, workloads, collections, evaluations, and reports use new output
directories and a `bundle.json` containing every member hash and a bundle hash.
`airesearch verify PATH` checks these bundles. Hashes detect accidental alteration;
they are not signatures against an adversary who can replace manifests too.

A run has an immutable `frozen/` bundle and mutable execution records. The frozen
bundle includes copied workload inputs/references, resolved settings, schema,
prompt template and rendered prompts, context, runner source, dependency lock,
and dependency/runtime provenance. It has no runtime dependence on the original
dataset directory. Its top-level manifest records sample, workload, and bundle IDs.

Each attempt copies fresh inputs into its own workspace, writes its exact prompt
and invocation, and retains native output. A retry never reuses a mutated prior
workspace. Atomic JSON writes and run locks prevent competing orchestrators from
partially replacing shared state. Child process groups are terminated on timeout
or cancellation; Docker containers are explicitly removed as well.

The first preflight freezes the observed CLI identity in `runtime.json`. Later
executions reject a different identity. Running also rejects a changed engine
source inventory. To execute the retained implementation after source changes:

```bash
uv run python runs/RUN/frozen/runner/entry.py run --run runs/RUN
```

Recreate the dependency environment from the retained lockfile when reproducing
on another machine. The engine checks its own source; it does not automatically
reinstall old dependencies or restore a remote model revision.

## Boundaries

Raw reference records are never copied into task workspaces or rendered prompts.
Local execution does not enforce a read boundary around those workspaces. Docker
mounts only the current workspace, response schema, and output directory. Its
image must be prepared without private datasets or experimental instructions.
Network policy and remote-service behavior remain part of the experimental
condition. Built-in harness prompts and server-side model changes cannot be
fully captured by this client; record versions and collection dates accordingly.
