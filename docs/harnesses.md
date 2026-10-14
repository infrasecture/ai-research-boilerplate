# Harness adapters and environments

An adapter validates its options, checks the installed CLI, constructs argv,
extracts the final response, and normalizes reported usage. The orchestrator owns
the subprocess, deadline, workspace, attempts, and logging. Arguments are passed
as an array; user prompts are streamed through stdin, never interpolated into a shell.

## Codex

The adapter uses `codex exec` with JSONL events, a response schema, and a retained
final-message file. It starts ephemeral sessions, ignores user config/rules,
disables approvals, and uses the configured read-only or workspace-write policy.
The default profile disables project instruction loading and web search.
Additional Codex configuration is explicit in `[options.config]`; each value is
a TOML expression passed as a separate `-c` argument. Record provider-specific
configuration here if your provider needs settings normally in a user config.

Supported options: `reasoning_effort`, `sandbox`, `config`.
No model name is supplied by the engine. Authentication remains the CLI's
responsibility. Ignoring user configuration can require explicitly configuring
your provider even when normal interactive Codex works.

The adapter was checked against local CLI help for `codex-cli 0.160.0` and
[official noninteractive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).
Preflight checks required flags and records version/executable identity; a real
authenticated smoke test is still required before gathering research data.

## Claude Code

The adapter uses print mode, a JSON Schema, and streaming JSON. It reads the final
`result` event's `structured_output`, falling back to a JSON-valued `result` text.
Safe mode, empty setting sources, strict empty MCP configuration, disabled slash
commands, and no session persistence make ambient customizations explicit.
Permissions use `dontAsk`; tools default to an empty list for inline demo tasks.

Supported options: `effort`, `tools` (comma-separated), `max_budget_usd`.
For file-based tasks, configure tools such as `Read,Glob,Grep` and verify the
intended access behavior in the selected environment. The adapter does not bypass
permission checks. A tool request denied by the harness remains in its logs.

The interface was checked against local CLI help for `2.1.204 (Claude Code)` and
the official [programmatic execution](https://code.claude.com/docs/en/headless)
and [CLI reference](https://code.claude.com/docs/en/cli-reference) pages. Newer or
older versions must pass preflight; optional flags/settings may still need an
adapter update. Pin exact versions for comparable collection.

## Mock

The mock is a real independent subprocess that solves the synthetic arithmetic
inputs without consulting reference data. `strategy = "constant_zero"` provides
a deliberately imperfect baseline. `delay_seconds`, `fail`, and `invalid` support
failure/recovery tests. Mock usage counters stay null: no model tokens are invented.

## Local execution

Only names in `inherit_env` are passed to child harnesses. Their values are never
written to invocation metadata. The supplied profile includes HOME and relevant
authentication variables so the CLIs can use existing credentials. Raw native
logs are retained unredacted and may contain sensitive task content.

Separate workspaces prevent accidental file collisions. They do not prevent
reads outside the workspace, and native read-only modes primarily constrain
writes. Do not use this as evidence of blinded access to reference answers.

## Docker execution

Prepare an image containing the chosen harness version, runtime libraries, and
permitted tools. Use a clean image without your dataset, user instructions, or
prior session history. Pin it by registry digest, for example:

```bash
export RESEARCH_IMAGE='your-registry/research-runtime@sha256:YOUR_64_HEX_DIGEST'
```

Copy the desired experiment config and select `../environments/docker.toml`.
The engine requires a digest-pinned image and Docker on the host. Preflight runs
CLI version/help commands in that image. Execution mounts only:

- The current attempt's workspace at `/workspace`.
- Its schema, read-only, at `/contract/schema.json`.
- Its output directory at `/outputs`.

It passes named credential variables using `docker --env NAME`, never values in
argv. For Codex automation, `CODEX_API_KEY` is supported by the official
[noninteractive authentication interface](https://learn.chatgpt.com/docs/non-interactive-mode#authenticate-in-automation).
The image must support the configured CLI authentication mechanisms. Do not mount
the entire project, home, Docker socket, or reference store into the agent image.
The runner does not build images or copy authentication files automatically.

`network = "bridge"` permits provider connectivity; it also permits other network
access the harness/tools can perform. `none` is suitable only when the task needs
no remote provider. Fine-grained egress control belongs in the experiment host's
network setup. Container removal is explicit on timeout/cancellation.

This implementation is validated through command-construction tests. It does not
claim a live Docker isolation test on the current development host. Before blinded
collection, test that a deliberately placed private sentinel cannot be read,
while assigned inputs and required model connectivity work.
