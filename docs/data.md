# Gathering and preparing data

The interchange format is UTF-8 JSONL: one object per nonempty line. Required
fields are `id` (a stable, unique path-safe identifier) and `input` (any JSON
value). Optional fields are `reference`, `files`, `group`, and `metadata`.

```json
{"id":"case-001","input":{"question":"Review the supplied module"},"files":["module.py"],"reference":{"expected":"your scoring target"},"group":"project-A","metadata":{"source_revision":"abc123"}}
```

`input` and the listed files are agent-visible. `reference`, `group`, and
`metadata` stay private. Put deliberate public context in `input`. `files` are
relative paths resolved against the JSONL file's directory or `--files-root`.
Traversal and symlinks are rejected. Text and binary file bytes are copied
unchanged, with each example's files grouped under `files/<sample-id>/`.

An absent `reference` means no known answer. Explicit `"reference": null` is a
known JSON-null answer. `group` should identify correlated observations; the
default is the sample ID. For overlapping source examples, preserve a common
source identity here. Metadata may hold acquisition provenance and exclusions
recorded by your project-specific source importer.

## Acquire from local sources

Export existing data to this JSONL format, then ingest it:

```bash
uv run airesearch prepare-data --source /path/to/records.jsonl \
  --files-root /path/to/source-files --output data/prepared/corpus-v1
```

The source JSONL is retained in `private/source.jsonl`; normalized public inputs,
private references, source checksum, and copied file hashes are sealed together.
The importer validates unique IDs and all file paths before publishing its output.
It does not infer labels, strip comments, or sanitize domain-specific leakage:
perform those transformations in a versioned source importer and document them.

## Acquire a remote JSONL file

```bash
uv run airesearch fetch --url 'https://example.org/versioned/records.jsonl' \
  --sha256 YOUR_EXPECTED_SHA256 --output data/raw/source-v1
uv run airesearch prepare-data --source data/raw/source-v1/source.jsonl \
  --output data/prepared/corpus-v1
```

The URL above is illustrative. Obtain the checksum from your source inventory.
Fetching requires HTTPS, verifies the supplied checksum before publishing, and
records the requested/resolved URLs and retrieval time. It does not crawl a site
or recursively download referenced files. Supply those files separately using
`--files-root`. For large acquisitions, use a dedicated importer and retain its
download manifest; this small fetcher reads one response into memory.

## Freeze the selection and packaging

```bash
uv run airesearch sample --dataset data/prepared/corpus-v1 \
  --size 100 --seed 42 --output samples/sample-v1
uv run airesearch package --sample samples/sample-v1 \
  --batch-size 5 --seed 7 --output workloads/batches-v1
```

Sampling is uniform without replacement. Omitting `--size` selects all records.
The seed and selected IDs are retained. Packaging shuffles selected examples
with its own seed and forms batches without dropping or resampling examples.
One example's file set is never split. Group membership is preserved for analysis;
this initial packager does not force correlated groups into the same workload.

For stratified selection, train/test separation, or group-preserving packaging,
extend the preparation stage and record that policy. Keep the frozen sample the
same when comparing prompt/model conditions. Changing batch size changes the
workload fingerprint while retaining the sample fingerprint.
