"""Atomic files, immutable bundles, path checks, and local process locks."""

import csv
import hashlib
import json
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(
            json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode() + b"\n"
        )
    temporary.replace(path)


def jsonl(path, rows):
    Path(path).write_bytes(b"".join(canonical(row) + b"\n" for row in rows))


def csv_file(path, rows, fields):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


def safe_file(root, name):
    root, relative = Path(root).resolve(), Path(name)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"Unsafe relative path: {name}")
    target = root / relative
    if any(p.is_symlink() for p in [target, *target.parents] if p != root):
        raise ValueError(f"Symlinks are not accepted as input: {name}")
    if not target.is_file():
        raise ValueError(f"Missing input file: {target}")
    return target


def inventory(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlink in frozen bundle: {path}")
        if path.is_file() and path != root / "bundle.json":
            result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return result


def seal(root):
    files = inventory(root)
    fingerprint = digest(canonical(files))
    write(Path(root) / "bundle.json", {"format": 1, "sha256": fingerprint, "files": files})
    return fingerprint


def verify(root):
    manifest = read(Path(root) / "bundle.json")
    files = inventory(root)
    if files != manifest["files"] or digest(canonical(files)) != manifest["sha256"]:
        raise ValueError(f"Frozen bundle changed: {root}")
    return manifest["sha256"]


@contextmanager
def new_directory(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError(f"Destination exists; choose a new name: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "bundle"
        staged.mkdir()
        yield staged
        if destination.exists():
            raise ValueError(f"Destination appeared during preparation: {destination}")
        staged.rename(destination)


@contextmanager
def lock(path):
    # The runner deliberately targets POSIX hosts: process groups and flock.
    import fcntl

    with Path(path).open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f"Another process owns {path}") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def require_positive(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value
