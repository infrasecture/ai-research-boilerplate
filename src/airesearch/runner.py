"""Bounded parallel execution with retained attempts and explicit recovery."""

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from jsonschema import Draft202012Validator

from .config import load_run
from .harnesses import command, inspect_runtime, response, telemetry
from .storage import digest, lock, now, read, require_positive, write

VALID = {"success", "incomplete"}


def validate_response(value, schema, sample_ids):
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise ValueError("Response schema: " + errors[0].message)
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("predictions"), list)
        or not isinstance(value.get("unanswered"), list)
    ):
        raise ValueError("Response must contain predictions and unanswered arrays")
    if any(
        not isinstance(p, dict) or "sample_id" not in p or "answer" not in p
        for p in value["predictions"]
    ):
        raise ValueError("Every prediction needs sample_id and answer")
    ids = [p["sample_id"] for p in value["predictions"]] + value["unanswered"]
    if (
        not all(isinstance(i, str) for i in ids)
        or len(ids) != len(set(ids))
        or set(ids) != set(sample_ids)
    ):
        raise ValueError(
            "Each assigned sample must appear exactly once in predictions or unanswered"
        )
    return "incomplete" if value["unanswered"] else "success"


def attempts(run, task_id):
    return sorted((Path(run) / "tasks" / task_id / "attempts").glob("[0-9][0-9][0-9][0-9]"))


def preflight(run):
    run, _, settings = load_run(run, check_code=True)
    with lock(run / ".lock"):
        return _preflight(run, settings)


def _preflight(run, settings):
    runtime = inspect_runtime(settings)
    path = run / "runtime.json"
    if path.exists() and read(path) != runtime:
        raise ValueError("Harness runtime changed since preflight; prepare a new run")
    if not path.exists():
        write(path, runtime)
    return runtime


def stop(process, container_name):
    cleanup_error = None
    if container_name:
        try:
            subprocess.run(
                ["docker", "rm", "--force", container_name], capture_output=True, timeout=15
            )
        except (OSError, subprocess.SubprocessError) as error:
            cleanup_error = error
    if process:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        # A harness may exit before a background tool. Reap the whole group even
        # after its leader has finished, including descendants ignoring SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if cleanup_error:
        raise cleanup_error


def announce(message):
    print(message, file=sys.stderr, flush=True)


def evidence(attempt):
    result = {
        name: digest((attempt / name).read_bytes())
        for name in ("prompt.md", "invocation.json", "events.jsonl", "stderr.log", "response.json")
        if (attempt / name).is_file()
    }
    for directory in ("workspace", "outputs"):
        for path in sorted((attempt / directory).rglob("*")):
            name = path.relative_to(attempt).as_posix()
            if path.is_symlink():
                result[name] = "symlink:" + os.readlink(path)
            elif path.is_file():
                result[name] = digest(path.read_bytes())
    return result


def verify_attempt(attempt):
    status = read(Path(attempt) / "status.json")
    if status.get("evidence") is not None and evidence(Path(attempt)) != status["evidence"]:
        raise ValueError(f"Retained attempt evidence changed: {attempt}")
    return status


def execute(run, task, settings, runtime, cancelled):
    if cancelled.is_set():
        return None
    task_id = task["id"]
    existing = attempts(run, task_id)
    number = 1 + max((int(p.name) for p in existing), default=0)
    attempt = run / "tasks" / task_id / "attempts" / f"{number:04d}"
    attempt.mkdir(parents=True)
    shutil.copytree(run / "frozen/workload/tasks" / task_id, attempt / "workspace")
    (attempt / "outputs").mkdir()
    shutil.copyfile(run / "frozen/prompts" / (task_id + ".md"), attempt / "prompt.md")
    status = {
        "task_id": task_id,
        "attempt": number,
        "status": "running",
        "started_at": now(),
        "duration_seconds": None,
        "exit_code": None,
        "error": None,
        "runtime": runtime,
    }
    write(attempt / "status.json", status)
    process, container_name = None, None
    started = time.monotonic()
    announce(
        f"[{task_id}/{number:04d}] starting {settings['harness']['adapter']} ({settings['model']})"
    )
    try:
        argv, env, container_name = command(settings, attempt, run / "frozen/schema.json")
        write(
            attempt / "invocation.json",
            {
                "argv": argv,
                "cwd": str(attempt / "workspace"),
                "inherited_environment_names": settings["environment"]["inherit_env"],
                "container_name": container_name,
                "timeout_seconds": settings["timeout_seconds"],
            },
        )
        with (
            (attempt / "prompt.md").open("rb") as stdin,
            (attempt / "events.jsonl").open("wb") as stdout,
            (attempt / "stderr.log").open("wb") as stderr,
        ):
            process = subprocess.Popen(
                argv,
                cwd=attempt / "workspace",
                env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            status["pid"] = process.pid
            write(attempt / "status.json", status)
            last_update = started
            while process.poll() is None:
                elapsed = time.monotonic() - started
                if cancelled.is_set() or elapsed >= settings["timeout_seconds"]:
                    status["status"] = "cancelled" if cancelled.is_set() else "timeout"
                    stop(process, container_name)
                    break
                if time.monotonic() - last_update >= 30:
                    announce(
                        f"[{task_id}/{number:04d}] running {elapsed:.0f}s; raw log {attempt / 'events.jsonl'}"
                    )
                    last_update = time.monotonic()
                time.sleep(0.05)
            status["exit_code"] = process.wait()
        if status["status"] == "running":
            if status["exit_code"] != 0:
                status.update(
                    status="failed",
                    error=f"Harness exited {status['exit_code']}; see stderr.log and events.jsonl",
                )
            else:
                try:
                    value = response(settings["harness"]["adapter"], attempt)
                    write(attempt / "response.json", value)
                    status["status"] = validate_response(
                        value, read(run / "frozen/schema.json"), task["sample_ids"]
                    )
                except (OSError, ValueError, TypeError) as error:
                    status.update(status="invalid", error=str(error))
    except Exception as error:
        status.update(status="failed", error=str(error))
    finally:
        try:
            stop(process, container_name)
        except (OSError, subprocess.SubprocessError) as error:
            status["cleanup_error"] = str(error)
        status.update(
            finished_at=now(),
            duration_seconds=time.monotonic() - started,
            usage=telemetry(settings["harness"]["adapter"], attempt / "events.jsonl"),
        )
        status["evidence"] = evidence(attempt)
        write(attempt / "status.json", status)
    announce(f"[{task_id}/{number:04d}] {status['status']} ({status['duration_seconds']:.2f}s)")
    return status


def run_tasks(run, jobs=None, retry_failed=False, limit=None):
    run, manifest, settings = load_run(run, check_code=True)
    jobs = require_positive(settings["max_parallel"] if jobs is None else jobs, "jobs")
    if limit is not None:
        require_positive(limit, "limit")
    cancelled = threading.Event()
    with lock(run / ".lock"):
        runtime = _preflight(run, settings)
        pending = []
        for task in manifest["tasks"]:
            prior = attempts(run, task["id"])
            statuses = [verify_attempt(p) for p in prior]
            for path, state in zip(prior, statuses):
                if state["status"] == "running":
                    pid = state.get("pid")
                    if pid:
                        try:
                            os.kill(pid, 0)
                        except ProcessLookupError:
                            pass
                        else:
                            raise ValueError(
                                f"Unfinished attempt has a live PID {pid}; inspect and terminate it before recovery"
                            )
                    invocation = path / "invocation.json"
                    if invocation.exists() and read(invocation).get("container_name"):
                        stop(None, read(invocation)["container_name"])
                    state.update(
                        status="interrupted",
                        duration_seconds=None,
                        recovered_at=now(),
                        evidence=evidence(path),
                        usage=telemetry(settings["harness"]["adapter"], path / "events.jsonl"),
                    )
                    write(path / "status.json", state)
            if any(s["status"] in VALID for s in statuses):
                continue
            if not prior or retry_failed:
                pending.append(task)
        if limit:
            pending = pending[:limit]
        announce(f"Scheduling {len(pending)} workloads with at most {jobs} concurrent sessions")
        start = time.monotonic()
        execution_id = len(list((run / "executions").glob("*.json"))) + 1
        execution = {
            "started_at": now(),
            "jobs": jobs,
            "retry_failed": retry_failed,
            "task_ids": [t["id"] for t in pending],
        }
        write(run / "executions" / f"{execution_id:04d}.json", execution)
        previous_handlers = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[sig] = signal.signal(sig, lambda signum, frame: cancelled.set())
        results = []
        try:
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                futures = [
                    pool.submit(execute, run, task, settings, runtime, cancelled)
                    for task in pending
                ]
                try:
                    for future in as_completed(futures):
                        state = future.result()
                        if state:
                            results.append(state)
                except BaseException:
                    cancelled.set()
                    raise
        finally:
            cancelled.set()
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
            execution.update(finished_at=now(), duration_seconds=time.monotonic() - start)
            write(run / "executions" / f"{execution_id:04d}.json", execution)
    summary = status(run)
    summary["executed_attempts"] = len(results)
    return summary


def status(run):
    run, manifest, _ = load_run(run)
    states = {}
    for task in manifest["tasks"]:
        prior = attempts(run, task["id"])
        state = verify_attempt(prior[-1])["status"] if prior else "pending"
        states[task["id"]] = state
    return {
        "run_id": manifest["run_id"],
        "tasks": states,
        "complete": all(s == "success" for s in states.values()),
    }
