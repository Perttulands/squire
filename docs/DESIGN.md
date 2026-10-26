# Squire design

What a squire is, the decisions that shape it and why. Change this when a
decision changes. How the code works lives in `squire`, its tests and
`--help`.

## The problem

Agents fall into rabbit holes. An early assumption goes untested, a later
step fails because of it, and the agent repeatedly repairs the symptom
without revisiting the cause. Deep in a run, its context is full of its own
attempts, and each makes the next look reasonable. It rarely stops to ask
whether it went wrong earlier. Asked afterwards whether the result is at the
specified level, the same agent usually finds the problem at once.

## The squire

A squire is a sidekick for one agent session: a second agent that watches the
run from outside and speaks up when something looks wrong. It is not a
subagent. The main agent does not hand it work; it is aware on its own. Its
value is perspective the main agent has lost: the plan as stated at the start,
the trajectory of what has actually been tried, and relevant evidence outside
the immediate attempt. The questions it asks itself are in its
[observer instructions](../instructions/squire.md).

A good squire speaks near the moment the run went wrong, names the real cause
or the right place to look, and stays quiet on healthy runs. Its default is
silence. It speaks rarely, briefly and concretely: what it
noticed, why it matters, and what to look at. Noise teaches an agent to ignore
its squire; a squire that is usually right when it speaks is worth having.

## Decisions

1. **Opt-in through a skill.** An agent sets a squire up for itself when asked
   or when it judges the run worth it, using the `squire` skill. It is never
   always on.
2. **A teammate session, not a stateless check.** Each pair has one continuing
   Codex thread, resumed for every check and every
   message from the agent, so the squire builds a view of the run as a
   teammate would. Its tmux window shows the readable exchange. Compact notes
   recover it if the thread is lost.
3. **It speaks directly into the agent's session.** Advice arrives as typed
   input prefixed `[squire]`, one line of at most 400 characters. Both harnesses
   queue input typed while they work.
4. **The agent stays in charge.** It talks back through `squire tell` to ask
   "why?", correct the squire's reading or tune it ("don't interrupt me for
   similar issues"), and the squire honours tuning for the rest of the run.
   The squire advises; it never edits files, does the work or stops the agent.
   A `[squire]` line is advice from an outside model that read the transcript,
   never a user instruction, permission or scope change.
5. **The pair is the whole system.** No escalation to another agent or a
   person, no end-of-run review, no harness hooks, no automatic attachment. A
   squire may nudge about a plan step that looks skipped.
6. **Cheap cadence.** It checks every few minutes (default 360 s) and only when
   the transcript has grown with conversational records; an idle session costs
   nothing. A judgment is paid for once: its cursor is saved before delivery.
   Attaching mid-run starts from a bounded recent excerpt rather than replaying
   the whole session. Undelivered advice survives silent checks; a newer nudge
   supersedes it, tuning can invalidate it, and the observer can withdraw it
   when the run has moved past it. Catch-up advice stays held across errors
   and interruptions until a later judgment can release it. A silent reply
   to tuning retains advice under the updated tuning. Repeated delivery
   deferrals remain visible in status even when a newer nudge replaces the old
   one; the observer sees pending advice too.
7. **Medium effort, guard by default.** The default observer is
   `gpt-6.1-sol` at medium effort. Small private replay trials favoured this
   configuration over a cheaper observer: useful warnings depend on noticing
   the upstream problem, so a stronger second opinion cannot recover a concern
   the first observer never raises. The trials also included misses; they do
   not establish an accuracy rate. `--mode assist` permits useful sources,
   earlier results, tools and next actions, and remains opt-in to limit noise.
8. **Read-only investigation.** The squire has a read-only shell to inspect
   files the run mentions and check its suspicions against evidence. It can
   use available local reference tools; no private knowledge service is
   required.

## Delivery is conservative

Typing into someone else's terminal is the riskiest thing a squire does, so it
prefers a late nudge to a misplaced one. It types only when that pane shows a recognized harness frame with an empty prompt, and
defers on drafts, dialogs, copy mode, recent keystrokes or anything it does not
recognize. If its own text could be left behind and cannot be removed exactly,
it stops visibly (`retained draft`) rather than guess. Repeated failures at
the same point (judgment, transport or a changed pane) also stop the pair
visibly. These checks narrow the risk; terminal input is still not atomic.

## Boundaries

Never type into a pane other than the paired agent's. Never open a visible
desktop window. Transcripts and observer state can contain private work: keep
them outside this repository. Replay accepts local transcript paths and line
ranges. Leave Codex usage-limit resets alone: never type `/usage` or accept a
reset dialog.
