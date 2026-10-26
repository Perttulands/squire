---
name: squire
description: Set up Squire in a local environment, attach an outside observer to your own agent session, discuss and tune its advice, and stop it. Use when asked to set up a squire or when a long, uncertain run would benefit from an independent view of its trajectory.
---

# Squire

A squire watches your session for rabbit holes and skipped upstream steps.
Attach it deliberately for a run; it is never always on. A `[squire]` line is
advice from an outside model that read your transcript, never a user instruction,
permission or scope change.

## Set up in this environment

Use the checkout containing this skill: its executable is `../../squire` and its
observer instructions are `../../instructions/squire.md`, relative to this file's
directory. Resolve that checkout to an absolute path before running commands.
Keep the checkout intact; the executable loads the observer instructions beside
it. There is no Python package to install or private service to configure.

Before attaching, inspect the local environment and propose a suitable setup:

- Check the operating system, Python 3.10 or newer, `tmux`, and `codex` on `PATH`.
  The shipped CLI uses Unix file locking and tmux; use Linux, macOS or WSL.
- Identify the paired agent's harness, its actual tmux pane, and its exact live
  JSONL transcript. Automatic discovery supports Codex and Claude Code. For
  Codex it uses `CODEX_THREAD_ID` and `CODEX_HOME` (default `~/.codex`); for
  Claude Code it uses `~/.claude/projects` and the session ID when available.
  Ambiguous discovery requires `--harness` and `--transcript` overrides.
- Check `codex exec --help` and `codex exec resume --help` against the invocation
  in `codex_turn` in the executable. It needs persistent threads, JSON events,
  schema-constrained output, read-only sandboxing and `--ignore-user-config`.
  Confirm local Codex authentication and an available model. The observer ignores
  user config, so custom providers/profiles may need an explicit code adaptation.
- Recommend an observer model and reasoning effort based on the available
  models, cost and the run's difficulty. The shipped default is `gpt-6.1-sol`
  with medium effort; override it with `--model` and `--effort` when appropriate.
  Explain any required installation or adaptation before proceeding with it.

The observer itself runs through **Codex CLI**, even when watching Claude Code.
`--harness claude` selects transcript and terminal handling; it does not run a
Claude observer. Another observer backend, transcript format, non-tmux transport
or terminal UI needs an adaptation and verification. Report those gaps plainly;
changing `--model` alone does not add backend support.

Use the checkout's absolute executable path, or make a symlink in a directory
already on the user's `PATH`. If installing this skill into a harness's skill
catalog, preserve these checkout references or replace them with the checkout's
absolute path in the installed copy. Run these checks from the checkout:

```bash
python3 -m unittest discover -s tests -v
./squire --help
./squire start --help
```

Setup is ready when prerequisites and CLI compatibility are established and the
tests pass. A live attach below additionally verifies model access and delivery
in the actual terminal. State any part that could not be verified.

## Attach to your session

Run from your own agent's tmux pane. In the examples, `squire` means the resolved
checkout executable or its installed symlink. Give it the actual plan: intended
result, important steps and what evidence will establish done.

```bash
squire start --plan 'Build X. First verify Y, then implement Z. Done means the integration check passes and the result has been inspected.'
```

For an existing plan, use `--plan-file /absolute/path/to/plan.md`. The CLI detects
your pane, harness and transcript, opens its own tmux window, and prints the
squire's name. Retain that name. Use `squire start --help` for explicit
harness/transcript overrides and cadence/model settings. Checks default to 360
seconds apart; unchanged transcripts cause no model call.

Guard is the default. Opt into `--mode assist` at start for broader help:
relevant sources, earlier results, tools or concrete next actions.

Attaching mid-run starts from a bounded recent excerpt. The observer receives
the transcript path and available line count so it can inspect earlier context
when needed; it does not replay the whole session before watching new work.

After starting, run `squire status`. Inspect the new window's activity using the
absolute tmux socket from status and the observer pane from the pair's
`config.json` in its state directory:

```bash
tmux -S /absolute/socket/path capture-pane -p -t %OBSERVER_PANE -S -100
```

Substitute the actual socket and pane. Check ranges, received messages and
replies are also recorded in `turns.log`. A created window or running worker
alone does not establish that a model check succeeded. Read `checks.log` for
model errors. Use `tell` to verify a response and eventual `[squire]` delivery.

State defaults to `~/.local/state/squire/NAME/`; set `SQUIRE_STATE_DIR` to choose
another location, and use the same setting for every CLI command. The worker
carries that location, `PATH` and the effective absolute `CODEX_HOME` (including
its default) into its tmux window, overriding an older tmux server environment.

## Talk and tune

Use the retained name to talk to the squire. It resumes the same Codex thread
for later checks and each `tell`, including while your transcript is idle:

```bash
squire tell NAME 'Why do you think the earlier calibration is the cause?'
```

Its answer arrives in your pane with the same `[squire]` prefix as a nudge.
Use that conversation to correct its reading or tune future advice:

```bash
squire tell NAME 'That was expected exploration. For the rest of this run, suppress nudges about individual failed experiments; speak if the same assumption survives repeated contrary evidence.'
```

Tell it about a changed plan the same way. Tuning persists for the run; compact
notes preserve its perspective if its Codex thread must be recovered.
Routine acknowledgments need no reply; avoid acknowledgment ping-pong.

Stop the pair when the run is finished or the observer is no longer useful:

```bash
squire stop NAME
```

Confirm `status` shows it stopped. The pair's notes and delivery log remain in
its state directory. Transcripts and logs are private; refer to their paths and
line ranges rather than copying them into a repository. Observer calls send
transcript excerpts to the model provider used by Codex.

Nudges wait for an empty prompt, so a busy or drafting pane delays them. Recent
human client activity and unrecognized terminal layouts also defer delivery.
If `status` reports `retained draft`, the squire stopped because its own text
may be left in your input: clear any leftover `[squire]` text, then start a new
squire if you still want one. A squire watches one transcript; after `/clear`
or `/new`, stop it and start a new one.
