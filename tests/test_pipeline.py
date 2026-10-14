import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from airesearch import data, evaluation
from airesearch.cli import main, sweep
from airesearch.config import load_config, prepare, render
from airesearch.harnesses import command, response, telemetry
from airesearch.runner import attempts, preflight, run_tasks, stop, validate_response
from airesearch.storage import read, verify, write

REPO = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dataset, self.sample, self.workload = [
            self.root / p for p in ("dataset", "sample", "workload")
        ]
        data.prepare_data(REPO / "examples/arithmetic.jsonl", self.dataset)
        data.sample(self.dataset, self.sample)
        data.package(self.sample, self.workload, batch_size=2)

    def tearDown(self):
        self.temporary.cleanup()

    def make_config(self, name="condition", options="", timeout=30, model="fixture"):
        profile = self.root / f"{name}-profile.toml"
        profile.write_text('adapter = "mock"\nexecutable = "python"\n[options]\n' + options)
        config = self.root / f"{name}.toml"
        values = {
            "id": name,
            "workload": str(self.workload),
            "prompt": str(REPO / "prompts/arithmetic.md"),
            "response_schema": str(REPO / "schemas/predictions.schema.json"),
            "harness_profile": str(profile),
            "environment": str(REPO / "configs/environments/local.toml"),
            "model": model,
            "timeout_seconds": timeout,
            "max_parallel": 4,
        }
        text = "".join(f"{k} = {json.dumps(v)}\n" for k, v in values.items())
        text += f"\n[context]\nprotocol = {json.dumps(str(REPO / 'prompts/protocol.md'))}\n"
        config.write_text(text)
        return config

    def make_run(self, name="condition", options="", timeout=30):
        run = self.root / name
        prepare(self.make_config(name, options, timeout), run)
        return run

    def score(self, run, name):
        collection, report = self.root / (name + "-collection"), self.root / (name + "-report")
        evaluation.collect(run, collection)
        evaluation.evaluate(collection, report)
        return report

    def test_full_pipeline_and_baseline_comparison(self):
        good = self.make_run("good")
        bad = self.make_run("baseline", 'strategy = "constant_zero"')
        self.assertTrue(run_tasks(good)["complete"])
        self.assertTrue(run_tasks(bad)["complete"])
        reports = [self.score(good, "good"), self.score(bad, "baseline")]
        self.assertEqual(read(reports[0] / "summary.json")["metrics"]["end_to_end_score"], 1)
        self.assertEqual(read(reports[1] / "summary.json")["metrics"]["end_to_end_score"], 0.25)
        comparison = self.root / "comparison"
        evaluation.compare(reports, comparison)
        self.assertEqual(len((comparison / "paired.csv").read_text().splitlines()), 9)
        verify(comparison)

    def test_parallel_processes_overlap_and_resume_skips(self):
        run = self.make_run(options="delay_seconds = 0.3")
        result = run_tasks(run, jobs=4)
        states = [read(p / "status.json") for p in run.glob("tasks/*/attempts/*")]
        latest_start = max(datetime.fromisoformat(s["started_at"]) for s in states)
        earliest_end = min(datetime.fromisoformat(s["finished_at"]) for s in states)
        self.assertLess(latest_start, earliest_end)
        self.assertEqual(result["executed_attempts"], 4)
        self.assertEqual(run_tasks(run)["executed_attempts"], 0)

    def test_retry_retains_all_attempts_and_uses_fresh_workspace(self):
        run = self.make_run()
        with patch(
            "airesearch.runner.response", side_effect=ValueError("transient transport output")
        ):
            self.assertFalse(run_tasks(run, limit=1)["complete"])
        self.assertEqual(run_tasks(run, limit=1)["executed_attempts"], 1)  # next pending task
        result = run_tasks(run, retry_failed=True)
        self.assertTrue(result["complete"])
        self.assertEqual(len(attempts(run, "task_0001")), 2)
        first, second = attempts(run, "task_0001")
        self.assertNotEqual(first / "workspace", second / "workspace")
        report = self.score(run, "retried")
        summary = read(report / "summary.json")
        self.assertEqual(summary["attempts"], 5)
        self.assertEqual(summary["resources"]["duration_seconds"]["known_measurements"], 5)
        self.assertEqual(summary["resources"]["total_tokens"]["unknown_measurements"], 5)

    def test_timeout_is_not_a_valid_empty_answer(self):
        run = self.make_run(options="delay_seconds = 2", timeout=1)
        run_tasks(run, limit=1)
        state = read(attempts(run, "task_0001")[0] / "status.json")
        self.assertEqual(state["status"], "timeout")
        with self.assertRaises(ProcessLookupError):
            os.kill(state["pid"], 0)
        metrics = read(self.score(run, "timeout") / "summary.json")["metrics"]
        self.assertEqual(metrics["coverage"], 0)
        self.assertEqual(metrics["end_to_end_score"], 0)
        self.assertIsNone(metrics["answered_score"])

    def test_partial_answer_preserves_denominator_and_is_not_retried(self):
        run = self.make_run()

        def partial(adapter, attempt):
            value = response(adapter, attempt)
            value["unanswered"] = [value["predictions"].pop()["sample_id"]]
            return value

        with patch("airesearch.runner.response", side_effect=partial):
            run_tasks(run)
        self.assertEqual(run_tasks(run, retry_failed=True)["executed_attempts"], 0)
        metrics = read(self.score(run, "partial") / "summary.json")["metrics"]
        self.assertEqual(metrics["coverage"], 0.5)
        self.assertEqual(metrics["end_to_end_score"], 0.5)
        self.assertEqual(metrics["answered_score"], 1)

    def test_invalid_response_is_retained_and_unscored(self):
        run = self.make_run(options="invalid = true")
        run_tasks(run, limit=1)
        attempt = attempts(run, "task_0001")[0]
        self.assertEqual(read(attempt / "status.json")["status"], "invalid")
        self.assertEqual(read(attempt / "response.json"), {"invalid": True})
        self.assertEqual(
            read(self.score(run, "invalid") / "summary.json")["metrics"]["answered"], 0
        )

    def test_nonzero_exit_is_failed(self):
        run = self.make_run(options="fail = true")
        run_tasks(run, limit=1)
        state = read(attempts(run, "task_0001")[0] / "status.json")
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["exit_code"], 3)

    def test_frozen_input_mutation_is_rejected(self):
        run = self.make_run()
        (run / "frozen/prompts/task_0001.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "Frozen bundle changed"):
            run_tasks(run)

    def test_response_and_generated_artifact_mutation_are_rejected(self):
        run = self.make_run()
        run_tasks(run, limit=1)
        attempt = attempts(run, "task_0001")[0]
        (attempt / "outputs/native-response.json").write_text("changed")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            evaluation.collect(run, self.root / "corrupt")

    def test_source_config_changes_do_not_mutate_run(self):
        config = self.make_config()
        run = self.root / "run"
        prepare(config, run)
        config.write_text("broken = true")
        self.assertTrue(run_tasks(run)["complete"])

    def test_manifest_task_mutation_is_rejected(self):
        run = self.make_run()
        manifest = read(run / "manifest.json")
        manifest["tasks"] = manifest["tasks"][:1]
        write(run / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "manifest disagrees"):
            run_tasks(run)

    def test_recovery_preserves_unknown_duration(self):
        run = self.make_run()
        path = run / "tasks/task_0001/attempts/0001"
        write(path / "status.json", {"status": "running", "duration_seconds": None})
        run_tasks(run, limit=1)
        self.assertEqual(read(path / "status.json")["status"], "interrupted")
        self.assertIsNone(read(path / "status.json")["duration_seconds"])
        report = self.score(run, "recovered")
        self.assertEqual(
            read(report / "summary.json")["resources"]["duration_seconds"]["unknown_measurements"],
            1,
        )

    def test_cancellation_terminates_active_worker(self):
        run = self.make_run(options="delay_seconds = 30", timeout=60)
        proc = subprocess.Popen(
            [sys.executable, "-m", "airesearch", "run", "--run", str(run), "--jobs", "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            state_path = run / "tasks/task_0001/attempts/0001/status.json"
            while time.monotonic() < deadline:
                if state_path.exists() and read(state_path).get("pid"):
                    break
                time.sleep(0.03)
            else:
                self.fail("Worker did not start")
            proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 2)
            state = read(state_path)
            self.assertEqual(state["status"], "cancelled")
            with self.assertRaises(ProcessLookupError):
                os.kill(state["pid"], 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_runtime_change_is_rejected(self):
        run = self.make_run()
        preflight(run)
        with patch("airesearch.runner.inspect_runtime", return_value={"version": "changed"}):
            with self.assertRaisesRegex(ValueError, "runtime changed"):
                run_tasks(run)

    def test_same_sample_different_batches_can_compare(self):
        first = self.make_run("first")
        data.package(self.sample, self.root / "workload2", batch_size=4)
        self.workload = self.root / "workload2"
        second = self.make_run("second")
        run_tasks(first)
        run_tasks(second)
        evaluation.compare(
            [self.score(first, "first"), self.score(second, "second")], self.root / "compared"
        )

    def test_different_samples_cannot_compare(self):
        first = self.make_run("first")
        data.sample(self.dataset, self.root / "sample2", size=4)
        data.package(self.root / "sample2", self.root / "workload2")
        self.workload = self.root / "workload2"
        second = self.make_run("second")
        reports = [self.score(first, "first"), self.score(second, "second")]
        with self.assertRaisesRegex(ValueError, "same frozen sample"):
            evaluation.compare(reports, self.root / "incompatible")

    def test_unknown_config_and_environment_variables_fail(self):
        config = self.make_config(model="${RESEARCH_TEST_UNSET_12345}")
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "unset"):
            load_config(config)
        config = self.make_config()
        config.write_text("unexpected = 1\n" + config.read_text())
        with self.assertRaises(ValueError):
            load_config(config)

    def test_sweep_prepares_without_executing(self):
        configs = [self.make_config("a"), self.make_config("b")]
        sweep_config = self.root / "sweep.toml"
        sweep_config.write_text("conditions = " + json.dumps([str(p) for p in configs]))
        result = sweep(sweep_config, self.root / "sweep")
        self.assertEqual(len(result["runs"]), 2)
        self.assertFalse(list((self.root / "sweep").glob("*/tasks")))

    def test_cli_reports_incomplete_run(self):
        run = self.make_run()
        self.assertEqual(main(["run", "--run", str(run), "--limit", "1"]), 2)

    def test_native_adapter_processes_with_fake_clis(self):
        executable = self.root / "fake-cli"
        executable.write_text(
            "#!" + sys.executable + "\n"
            "import json, pathlib, sys\n"
            "if '--version' in sys.argv:\n"
            "    print('fake-cli 1'); raise SystemExit(0)\n"
            "if '--help' in sys.argv:\n"
            "    print('--json --output-schema --output-last-message --ephemeral --ignore-user-config --ignore-rules --print --json-schema --output-format --no-session-persistence --safe-mode'); raise SystemExit(0)\n"
            "sys.stdin.read()\n"
            "task = json.loads(pathlib.Path('task.json').read_text())\n"
            "value = {'predictions': [{'sample_id': row['id'], 'answer': 0} for row in task['samples']], 'unanswered': [], 'notes': 'fixture'}\n"
            "if '--output-last-message' in sys.argv:\n"
            "    pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text(json.dumps(value))\n"
            "    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':10}}))\n"
            "else:\n"
            "    print(json.dumps({'type':'result','structured_output':value,'usage':{'input_tokens':10,'cache_read_input_tokens':80,'cache_creation_input_tokens':20,'output_tokens':5}}))\n"
        )
        executable.chmod(0o755)
        for adapter in ("codex", "claude-code"):
            with self.subTest(adapter=adapter):
                config = self.make_config(adapter)
                (self.root / f"{adapter}-profile.toml").write_text(
                    f"adapter = {json.dumps(adapter)}\nexecutable = {json.dumps(str(executable))}\n"
                    'version = "fake-cli 1"\n[options]\n'
                )
                run = self.root / adapter
                prepare(config, run)
                self.assertTrue(run_tasks(run)["complete"])
                report = self.score(run, adapter)
                summary = read(report / "summary.json")
                self.assertEqual(summary["metrics"]["end_to_end_score"], 0.25)
                self.assertEqual(summary["resources"]["total_tokens"]["known_measurements"], 4)


class ContractTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "Uses Linux process-state inspection")
    def test_cleanup_kills_descendant_after_parent_exits(self):
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid,flush=True)",
            ],
            stdout=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            child_pid = int(process.stdout.readline())
            process.wait(timeout=5)
            stop(process, None)
            deadline = time.monotonic() + 2
            child_stat = Path(f"/proc/{child_pid}/stat")
            while child_stat.exists() and child_stat.read_text().split()[2] != "Z":
                if time.monotonic() > deadline:
                    self.fail("Background child survived process-group cleanup")
                time.sleep(0.01)
        finally:
            stop(process, None)
            process.stdout.close()

    def test_fetch_verifies_checksum_before_publishing(self):
        from io import BytesIO

        from airesearch.storage import digest

        with tempfile.TemporaryDirectory() as temporary:
            payload = b'{"id":"a","input":"x"}\n'
            response_stream = BytesIO(payload)
            response_stream.url = "https://example.org/records.jsonl"
            with patch("urllib.request.urlopen", return_value=response_stream):
                data.fetch(response_stream.url, digest(payload), Path(temporary) / "good")
            self.assertEqual((Path(temporary) / "good/source.jsonl").read_bytes(), payload)
            response_stream = BytesIO(payload)
            response_stream.url = "https://example.org/records.jsonl"
            with (
                patch("urllib.request.urlopen", return_value=response_stream),
                self.assertRaises(ValueError),
            ):
                data.fetch(response_stream.url, "0" * 64, Path(temporary) / "bad")
            self.assertFalse((Path(temporary) / "bad").exists())

    def test_prompt_rendering_does_not_interpret_inserted_data(self):
        self.assertEqual(render("{{ task_json }}", {"task_json": "{{ secret }}"}), "{{ secret }}")
        with self.assertRaises(ValueError):
            render("{{ missing }}", {})
        with self.assertRaises(ValueError):
            render("{{ bad-name }}", {})

    def test_response_ids_must_be_complete_unique_and_assigned(self):
        schema = read(REPO / "schemas/predictions.schema.json")
        for ids in (["a", "a"], ["b"], []):
            with self.assertRaises(ValueError):
                validate_response(
                    {
                        "predictions": [{"sample_id": i, "answer": 1} for i in ids],
                        "unanswered": [],
                        "notes": "",
                    },
                    schema,
                    ["a"],
                )

    def test_sources_cannot_escape_file_root_or_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "records.jsonl"
            source.write_text(json.dumps({"id": "a", "input": "x", "files": ["../secret"]}))
            with self.assertRaises(ValueError):
                data.prepare_data(source, root / "out")
            source.write_text('{"id":"a","input":0}\n{"id":"a","input":1}\n')
            with self.assertRaises(ValueError):
                data.prepare_data(source, root / "out")

    def test_private_reference_and_metadata_do_not_enter_workloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            (root / "source.txt").write_text("public content")
            source.write_text(
                json.dumps(
                    {
                        "id": "a",
                        "input": "public",
                        "files": ["source.txt"],
                        "reference": "PRIVATE_SENTINEL",
                        "metadata": {"private": "PRIVATE_SENTINEL"},
                    }
                )
            )
            data.prepare_data(source, root / "dataset")
            data.sample(root / "dataset", root / "sample")
            data.package(root / "sample", root / "workload")
            public = root / "workload/tasks/task_0001"
            self.assertEqual((public / "files/a/source.txt").read_text(), "public content")
            self.assertNotIn("PRIVATE_SENTINEL", (public / "task.json").read_text())
            self.assertFalse((public / "reference.json").exists())

    def test_null_reference_is_distinct_from_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jsonl"
            source.write_text('{"id":"a","input":0,"reference":null}\n{"id":"b","input":0}\n')
            data.prepare_data(source, root / "dataset")
            refs = read(root / "dataset/private/reference.json")
            self.assertTrue(refs["a"]["has_reference"])
            self.assertFalse(refs["b"]["has_reference"])

    def test_docker_mounts_only_current_workspace_schema_and_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            schema = root / "schema.json"
            write(schema, {"type": "object"})
            settings = {
                "model": "provided-model",
                "harness": {"adapter": "codex", "executable": "codex", "options": {}},
                "environment": {
                    "mode": "docker",
                    "image": "test@sha256:" + "a" * 64,
                    "inherit_env": ["RESEARCH_TEST_TOKEN"],
                },
            }
            with patch.dict(os.environ, {"RESEARCH_TEST_TOKEN": "secret-value"}):
                argv, env, container = command(settings, root / "attempt", schema)
            mounts = [argv[i + 1] for i, x in enumerate(argv) if x == "--mount"]
            self.assertEqual(len(mounts), 3)
            self.assertNotIn("secret-value", " ".join(argv))
            self.assertIn("/outputs/native-response.json", argv)
            self.assertTrue(container.startswith("airesearch-"))

    def test_native_telemetry_normalization_and_missing_usage(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 80,
                            "output_tokens": 10,
                        },
                    }
                )
                + "\n"
            )
            result = telemetry("codex", path)
            self.assertEqual(result["total_tokens"], 110)
            path.write_text(
                json.dumps(
                    {
                        "type": "result",
                        "usage": {
                            "input_tokens": 10,
                            "cache_read_input_tokens": 80,
                            "cache_creation_input_tokens": 20,
                            "output_tokens": 5,
                        },
                    }
                )
                + "\n"
            )
            self.assertEqual(telemetry("claude-code", path)["total_tokens"], 115)
            path.write_text("partial invalid event\n")
            self.assertIsNone(telemetry("codex", path)["total_tokens"])
            path.write_text(
                json.dumps({"type": "result", "usage": {"input_tokens": 10, "output_tokens": 2}})
            )
            self.assertIsNone(telemetry("claude-code", path)["total_tokens"])

    def test_claude_structured_output_and_error_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            attempt = Path(temporary)
            stream = attempt / "events.jsonl"
            stream.write_text(json.dumps({"type": "result", "structured_output": {"ok": True}}))
            self.assertEqual(response("claude-code", attempt), {"ok": True})
            stream.write_text(json.dumps({"type": "result", "is_error": True}))
            with self.assertRaises(ValueError):
                response("claude-code", attempt)


if __name__ == "__main__":
    unittest.main()
