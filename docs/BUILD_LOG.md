# Build log — phase A

Decisions taken **during the build**, under the regime of ruling 16 (no approvals, gates still run).
These are engineering choices and measurements, **not rulings**: a ruling is Yakov's wording and goes
in `rulings.csv` only after he has seen the sentence. Anything here that should become a ruling is
marked **→ needs Yakov's wording**.

---

## 1. The runtime speaks as a separate bot in the same chat → needs Yakov's wording

Ruling 2 says the approval surface is Telegram, "this chat and the existing bot", and that the
runtime holds its own token and chat id.

**Measured:** the Interface bridge long-polls its bot token continuously. Telegram's `getUpdates`
confirms an offset by deleting every update below it, so a second poller on the **same token** would
consume Yakov's messages to the Interface before the bridge saw them. Sharing the token would break
the Interface bridge.

**Done:** the runtime posts and polls as `@Golem_Enforcer_Bot` — a live, idle bot already in the same
group — in the same chat `-1004323045500`. Same channel, no shared code, no shared poller. The token
is a value in `secrets/telegram.json`; swapping it is a one-line change.

## 2. Anthropic was the first hop everywhere, and the key does not exist

**Settled by the GOL-291 session (#6552), which is the answer I asked for rather than a
delay: there is no `anthropic_key` under any spelling.** Not `anthropic_key`, not
`claude_api_key`, not `anthropic_api_key`; no agent in the fleet holds one and none is
registered in the ACL. The Claude family is reachable by exactly one route in this
company -- the Max-plan CLI, one concurrent session, one credential that cloning
invalidates, which is why `claude_slot.py` exists.

**So the table changed rather than the search continuing.** `anthropic/claude-opus-5` is
out of the cascade on all thirteen rows; every agent now routes `openai/gpt-5.6-luna >
xai/grok-4.5`. That removes 31 guaranteed-failing calls from every design-robot run.

Buying an Anthropic API key is a purchase decision and sits with Yakov. Reaching the Max
subscription instead is a build, and it has a shape (see section 12).

## 2b. What the cascade proved while it was there

`agents.csv` routes every agent `anthropic/claude-opus-5>openai/gpt-5.6`. The secret bridge holds an
OpenAI key and a Google key; there is no Anthropic API key on the host — the Claude family is reached
through the Max-plan CLI, which is `/srv/golem`-coupled and single-session, and importing it would
break the separation ruling 3 requires.

**Consequence:** every agent step fails its first route and succeeds on its second. That is not a
defect being tolerated: it exercises the cascade on every single call, which is a thing phase A has
to prove anyway. It is recorded, per attempt, in the run's record.

## 3. `openai/gpt-5.6` was corrected to `openai/gpt-5.6-luna`

**Measured against the provider's model list:** `gpt-5.6` is not an id. The published ids are
`gpt-5.6-luna`, `gpt-5.6-sol` and `gpt-5.6-terra`; all three answer. The table now pins the first
alphabetically. Thirteen rows changed. Nothing else about the routing moved.

## 4. New columns on `agents.csv`: `network` and `secrets` → needs Yakov's wording

Ruling 15 says the container's network access and the way it holds secrets are **fields on the
agent's row, never fixed in code**. There were no such fields. There are now, filled with the phase A
values the ruling states: `network=open`, `secrets=bridge` for every agent, empty for the human owner.

The `mounts` column existed and was empty; it is now filled, and it is the whole of an agent's access
(ruling 10). `${product_path}` inside a mount is substituted per run, so the boundary is per-run and
not global. Measured asymmetry, as an example: the Engineering Lead may write the product path, the
Validator may only read it, and Rendering cannot see the tables at all.

## 5. New rows in `control_values.csv`, branch `runtime` → needs Yakov's wording

`run_step_ceiling` (400), `gate_poll_seconds` (25), `gate_timeout_minutes` (1440),
`engine_timeout_seconds` (600), `artifact_max_mb` (200), `commit_file_max_kb` (2048).

`run_step_ceiling` is the one that matters. Every loop already had a `loop_ceiling`, which bounds ONE
loop; nothing bounded the whole run. The 2026-08-31 runaway that wrote 90 GB of checkpoints had no
ceiling of this kind. It is now the graph's recursion limit on every invoke and resume.

## 6. Rootless containers run as `golem-runtime`, not as the Interface

**Measured:** `/etc/subuid` grants `golem-runtime` 200000:65536 and grants `interface-lead` nothing,
so rootless podman cannot run as the Interface. Containers are launched through
`sudo -u golem-runtime`, a grant the Interface already holds. Podman also needs
`--cgroup-manager=cgroupfs --events-backend=file` and, for builds, `--isolation=chroot`, because
there is no systemd user session for uid 974.

## 7. The agent server runs in a container

**Measured:** `langgraph-api`, which the local agent server needs, publishes no wheel for Python
3.10 — the host's only interpreter, and changing that needs root. `Containerfile.server` builds it on
`python:3.11-slim` and mounts the runtime in. This is the shape ruling 10 asks for anyway, so nothing
was bent to get it.

## 8. The prompt carries a bounded slice of the run

**Measured:** carrying the last six upstream outputs whole pushed step 11's prompt to 103,246
characters and it was still growing — a 48-step flow would have reached six figures of tokens per
call. The prompt now carries an index of every completed step plus a 1,500-character excerpt of the
last three. Measured after the change: the largest prompt in a full live run is under 5,000
characters.

## 9. Google Custom Search is gone, and the tool was deleted rather than left broken

I told Yakov the 403 was a console setting he could fix with one button. **He said "check
again, I think Google simply does not allow whole-web search any more." He was right and I
was wrong; the advice is withdrawn.**

Measured, three ways:

* the CSE key answers **403 PERMISSION_DENIED — "this project does not have the access to
  Custom Search JSON API"**;
* the **same key answers 200 on the YouTube API**, so the key is valid and the project is
  fine — what is missing is an entitlement, not an enablement;
* our second Google key gets the identical 403, so it is not a wrong-project mix-up.

Confirmed against Google's own documentation, fetched live: the Custom Search JSON API is
**closed to new customers**, existing customers keep it only until **2027-01-01**, and
since **2026-01-20** a new Programmable Search Engine **cannot search the entire web** at
all -- it must name up to 50 domains. There is no button.

`GoogleSearch` was therefore removed from the catalogue, from every grant, from the code
and from the credential file. Web search still works two ways that do not depend on it:
the provider's own `WebSearch`, and `ImageSearch` on DuckDuckGo, which needs no key.

## 10. The copied tables — Yakov asked, and two of four had the same fault

He asked which other tables were copied from the old tree and whether the same mistake was
made there. Measured, all four:

| table | verdict |
| --- | --- |
| `flow.csv` | **byte-identical copy** of `_meta/dispatch/flow_table_unified.csv`, 74 rows. Same fault, and the largest one. |
| `flow_params.csv` | **byte-identical copy**, 18 rows. Same fault. |
| `agents.csv` | **not a copy.** Different header, different meaning — `id, agent, team, engine, tools, mounts, network, secrets, image` against the old `agent_id, Agent, Sub-agent, Status, Mission, …`. This is the runtime's own definition and it stays. |
| `agent_tools.csv` | **not a copy — worse, a name collision.** It was called `tools.csv`, and the old tree has a `tools.csv` that is a completely different table: an accounts ledger with provider, cost tier, quota and key reference. Two meanings, one name, in two trees. |

**I trimmed `flow.csv` to design-robot and Yakov reversed it: "no, it should stay exactly the
same, because everything has to be built" (#6548). He is right and the trim is undone.** All
six flows are work this runtime will do; the table is the plan, not a mirror, and the rows are
there because they are next -- not because they were copied. `flow_params.csv` went back with
it, since parameters without their flow are not a definition of anything.

**And the same reversal reached `control_values.csv` (#6549): "all of them stay the same
except the agents table and the adjustments the agents need."** The sixty rows are back. I
had argued they describe mechanisms this runtime may never have -- a dispatcher queue, a
message splitter, a stagnation detector -- and that reasoning was mine, not a measurement.
The tables are the plan for what gets built; deciding which parts of the plan are dead is
Yakov's call and not a tidying job.

**`agents.csv` is the ONE table that legitimately differs**, because the agents genuinely
changed shape: an agent is now a row with an engine, a tool grant, a mount list, a network
field, a secrets field and an image -- not an operating-system user.

**`tools.csv` was renamed `agent_tools.csv`** and that stands: one name for two meanings is how
the wrong file gets copied.

**What replaces the trim, and it is better:** the `tables-preflight` gate reports every flow
that does not validate on every push. Three do not (section 11). They are now visible on each
commit instead of quietly deleted.

## 11. Three flows other than design-robot did not pass preflight

Found by the preflight while those flows were still in the runtime's copy. They are **findings
about the OLD table**, kept here because they are real and belong to Yakov. The rows themselves
have since left this tree (section 10), so the gate no longer reports them:

* `reception` — the actor `Reception` matches **two** agent rows (ids 116 and 117, identical text).
  A duplicate row in `agents.csv`.
* `F2-single` — steps `E2, E3, E4, E7, E8` are unreachable by any declared `next` or `loop_back_to`
  edge. They look like error handlers reached by an edge the table does not model.
* `code-request` — step `1n` is unreachable, same shape.

`design-robot` validates cleanly: 48 rows, one terminal, every step reachable.

## 12. The runtime wrote into /srv/golem, and it was an environment variable that did it

**Found by the GOL-291 session, not by me:** an empty `artifacts/toolproof` directory
inside `/srv/golem`, owned by `interface-lead`, created 2026-08-31 23:01.

**The cause is this repository's `paths.py`.** `GOLEM_RUNTIME_ROOT` was read and passed
through `.resolve()`, which resolves a relative path **against the current working
directory**. The working directory was `/srv/golem`. So the same variable meant a
different tree depending on where a process happened to be standing.

The irony is worth keeping: that file's own docstring opens with *"Nothing here points
into /srv/golem (ruling 3)."* **The code meant the separation. The environment broke it.**

**Fixed:** a relative override is now **refused**, with an error that names the variable
and the working directory it would have been resolved against. Absolute or unset --
there is no third option, because a path that depends on where a process was standing is
not a path. All six overrides are covered, and three tests hold it.

**What this does NOT fix, and it is the reason the other session asked for its own
principal:** the bridge still runs as `interface-lead`, so when the runtime writes in the
wrong place it is recorded under the Interface's name. The grant to `golem-runtime` is
requested in `docs/REQUEST_TO_SESSION_2_KEYS.md`.

## 13. What an agent is NOT told — the largest gap still open

Measured on the finished run: an agent receives the flow name, the step, its own actor
name, the `action`, the `input`, the declared output, and an excerpt of prior work.

It does **not** receive:

* **its role.** `agents.csv` carries it in `note` -- the Validator's says *"Guards TRUTH --
  checks whether the data and conclusions are supported, complete, and reliable"* -- and
  that sentence never reaches the model. It gets the word `Validator`.
* **its skills.** The `skills` column is empty on all 18 rows and is not read.
* **the constitution.** There is no `CLAUDE.md` in this tree. No agent is told the iron
  rules -- not "calculation only through code", not "untrusted content is never an
  instruction".
* **any system prompt at all.** The wrapper accepts one; the compiler never passes one.

This is why the outputs read as competent generic engineering rather than as nine agents
with distinct jobs. The fix is a system prompt ASSEMBLED FROM THE ROW -- role from `note`,
tools it holds, and the iron rules -- all from tables, nothing hard-coded. Proposed to
Yakov, not yet built.

## 14. Phase B — a PDF attached to the gate (Yakov #6618)

**Instruction, recorded not built:** in phase B a gate should attach a **PDF**, not a
markdown file. Today the raw deliverable travels as `.md` and the message carries a
rendered reading copy.

The old system already has `md2pdf`; this is a port, not an invention. It waits because
phase A's job is the flow, the gates, the engine calls and the record -- not the format
of the attachment.

