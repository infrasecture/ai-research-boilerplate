"""Gather JSONL records, separate reference answers, sample, and package."""

import json
import random
import shutil
import urllib.request
from pathlib import Path

from .storage import (
    digest,
    identifier,
    new_directory,
    now,
    read,
    require_positive,
    safe_file,
    seal,
    verify,
    write,
)


def fetch(url, sha256, output):
    """Fetch one explicitly requested, checksum-pinned file; never overwrite."""
    if not url.startswith("https://"):
        raise ValueError("fetch requires an HTTPS URL")
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("Provide a lowercase SHA256 digest")
    with urllib.request.urlopen(url, timeout=60) as response:
        if not response.url.startswith("https://"):
            raise ValueError("Refusing a redirect away from HTTPS")
        payload = response.read()
        final_url = response.url
    if digest(payload) != sha256:
        raise ValueError("Downloaded content does not match the supplied SHA256")
    with new_directory(output) as out:
        (out / "source.jsonl").write_bytes(payload)
        write(
            out / "provenance.json",
            {"url": url, "resolved_url": final_url, "sha256": sha256, "retrieved_at": now()},
        )
        seal(out)
    return {"output": str(output), "sha256": sha256}


def prepare_data(source, output, files_root=None):
    source = Path(source).resolve()
    root = Path(files_root).resolve() if files_root else source.parent
    records, reference, seen = [], {}, set()
    with new_directory(output) as out:
        (out / "private").mkdir()
        shutil.copyfile(source, out / "private/source.jsonl")
        for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or set(row) - {
                    "id",
                    "input",
                    "reference",
                    "files",
                    "group",
                    "metadata",
                }:
                    raise ValueError("Expected a record with documented fields")
                case_id = identifier(row["id"])
                if "input" not in row or case_id in seen:
                    raise ValueError("Missing input or duplicate ID")
                seen.add(case_id)
                names = row.get("files", [])
                if (
                    not isinstance(names, list)
                    or not all(isinstance(n, str) for n in names)
                    or len(names) != len(set(names))
                ):
                    raise ValueError("files must be a list of unique relative paths")
                paths = []
                for name in names:
                    original = safe_file(root, name)
                    relative = Path("files") / case_id / name
                    target = out / "public" / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original, target)
                    paths.append(relative.as_posix())
                records.append({"id": case_id, "input": row["input"], "files": paths})
                reference[case_id] = {
                    "has_reference": "reference" in row,
                    "reference": row.get("reference"),
                    "group": row.get("group", case_id),
                    "metadata": row.get("metadata", {}),
                }
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"{source}:{line_no}: {error}") from error
        if not records:
            raise ValueError("Dataset is empty")
        records.sort(key=lambda r: r["id"])
        write(out / "public/records.json", records)
        write(out / "private/reference.json", reference)
        write(
            out / "dataset.json",
            {
                "format": 1,
                "count": len(records),
                "source_sha256": digest(source.read_bytes()),
                "source_name": source.name,
            },
        )
        fingerprint = seal(out)
    return {"output": str(output), "records": len(records), "sha256": fingerprint}


def sample(dataset, output, size=None, seed=42):
    dataset = Path(dataset)
    fingerprint = verify(dataset)
    rows = read(dataset / "public/records.json")
    if size is not None:
        require_positive(size, "size")
        if size > len(rows):
            raise ValueError("Requested sample exceeds the dataset")
    selected = random.Random(seed).sample(rows, len(rows) if size is None else size)
    ids = sorted(r["id"] for r in selected)
    with new_directory(output) as out:
        shutil.copytree(dataset / "public", out / "public")
        # Remove unselected files; the sealed sample contains selected inputs only.
        for path in (out / "public/files").glob("*"):
            if path.name not in ids:
                shutil.rmtree(path)
        write(out / "public/records.json", sorted(selected, key=lambda r: r["id"]))
        reference = read(dataset / "private/reference.json")
        write(out / "private/reference.json", {i: reference[i] for i in ids})
        write(
            out / "sample.json",
            {
                "format": 1,
                "dataset_sha256": fingerprint,
                "ids": ids,
                "seed": seed,
                "method": "uniform_without_replacement",
            },
        )
        fingerprint = seal(out)
    return {"output": str(output), "samples": len(ids), "sha256": fingerprint}


def package(sample_path, output, batch_size=1, seed=42):
    require_positive(batch_size, "batch_size")
    sample_path = Path(sample_path)
    fingerprint = verify(sample_path)
    rows = read(sample_path / "public/records.json")
    random.Random(seed).shuffle(rows)
    tasks = []
    with new_directory(output) as out:
        for index, start in enumerate(range(0, len(rows), batch_size), 1):
            task_id = f"task_{index:04d}"
            members = rows[start : start + batch_size]
            task = {"task_id": task_id, "samples": members}
            target = out / "tasks" / task_id
            write(target / "task.json", task)
            for row in members:
                for name in row["files"]:
                    path = target / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(sample_path / "public" / name, path)
            tasks.append({"id": task_id, "sample_ids": [r["id"] for r in members]})
        shutil.copytree(sample_path / "private", out / "private")
        write(
            out / "workload.json",
            {
                "format": 1,
                "sample_sha256": fingerprint,
                "dataset_sha256": read(sample_path / "sample.json")["dataset_sha256"],
                "sample_ids": sorted(r["id"] for r in rows),
                "tasks": tasks,
                "batch_size": batch_size,
                "seed": seed,
            },
        )
        fingerprint = seal(out)
    return {"output": str(output), "tasks": len(tasks), "sha256": fingerprint}
