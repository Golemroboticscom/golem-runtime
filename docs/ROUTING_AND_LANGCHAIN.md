# The routing layer, measured — and what LangChain can and cannot do with it

Yakov, #6648: go over the EXISTING routing functionality and say plainly what is possible in
LangChain and what is not; then give architectural alternatives and their implications.
His priority order, stated: **1. simplicity · 2. keep all functionality · 3. reuse what exists**
— reuse only third.

Nothing here is built. This is the survey and the choices.

---

## 1. What exists today, counted

`agents/interface/engines/` — 5,332 lines. The three that matter:

| file | lines | what it is |
| --- | --- | --- |
| `engine_router.py` | 684 | **decides** which engine + model. Never calls a model. |
| `engine_adapters.py` + `_meta/engine_adapters.csv` | 262 + 5 rows × 19 cols | **per-engine parameters and quirks**, layered |
| `engine_core.py` | 1,113 | the **agent loop** that performs it |
| `openrouter.py` | 673 | the transport, payload building, usage/cost capture |

### 1a. The decision — `engine_router.route()`, eight levels of precedence

1. per-agent state — Active / Inactive / Restricted (`agent_state.json`); Inactive blocks routing
2. emergency manual override (`override.json`)
3. explicit forced — a human's `/engine`, which must beat a stale rule
4. routing-rules table — `_meta/task_routing_rules.csv`, agent or keyword match
5. agent pin — the first model of the agent's `cascade` in `_meta/agent_engines.csv`
6. overflow — measured Claude queue backlog and weekly token budget
7. health fallback — the caller passes `fallback=` after the primary failed
8. no match → `NoRouteError`. **No hidden default.**

Plus a safety bias: a bare or very short message is PRESUMED high-stakes and goes to the strong tier.

### 1b. The parameters — `engine_adapters.resolve(engine, model, agent, contract)`

**Four layers, each overriding the last:** defaults → engine row → model row → agent row →
optional per-contract column (`<field>__<contract>`).

The 19 columns of `_meta/engine_adapters.csv`:

    max_steps · supports_temperature · preserve_reasoning_fields · system_message_placement
    builtin_tools_conflict · force_tool_choice_default · max_tokens_default · max_tokens_param
    prompt_addendum · ctx_byte_budget · tool_output_max_chars · eviction_strategy
    api_style · native_search · retry_attempts · retry_backoff_s · http_timeout_s

Every one of these was written because something failed live. Three examples, from the table's
own notes: OpenAI's reasoning models reject `max_tokens` and need `max_completion_tokens`
(`max_tokens_param`); Gemini rejects a system message anywhere but position 0 and needs
`thought_signature` preserved across tool turns (`system_message_placement`,
`preserve_reasoning_fields`); a single hardcoded 60,000-byte context budget was trimming Gemini
50–100× too early (`ctx_byte_budget`).

### 1c. The loop — `engine_core.run()`

Per-step engine and model hints (`_resolve_engine_hint(step_engines, step_models, pinned)`) ·
session persistence and resume · context trimming with a tool-output eviction strategy ·
stagnation detection · **structured termination** (`NEEDS_CLARIFICATION`, `CAPABILITY_GAP` — the
two a stronger engine cannot fix, returned to the human rather than escalated) · quality-failure
escalation to the next engine with fresh context · `guard.py` on every side effect ·
OS-user impersonation per agent · cost and token metering tagged by agent + Linear id.

### 1d. The catalogue

`_meta/engine_catalog.csv` — 19 models across 5 engines (claude, gemini, gpt, grok, deepseek).

---

## 2. What LangChain can do, knob by knob

Measured against what is installed in `/srv/runtime/lib`: `langchain_core 1.6.1`, `langgraph
1.2.11`. **No provider integrations are installed and the runtime imports no LangChain model
class at all** — today LangGraph is used for the graph and nothing else.

**Yes, natively:**

* **a specific model per flow step** — build a different chat-model object per node, or declare
  `configurable_fields` and pass the choice in at invoke time. This is normal LangChain.
* **temperature, max_tokens** — `.bind(temperature=…, max_tokens=…)` per call.
* **max steps** — LangGraph's `recursion_limit`, already in use.
* **a per-engine base prompt layer** — a different system message per node. Trivial in code.
* **cascade / fallback** — `.with_fallbacks([...])`.
* **retries and rate limiting** — `max_retries`, `InMemoryRateLimiter`.
* **the agent loop** — `create_react_agent`, available now.
* **raw token counts** — `usage_metadata` on every response.

**No, not natively — these have no LangChain equivalent:**

* **the routing DECISION.** LangChain has no router concept. Agent state, manual override, a
  rules table, agent pins, overflow on measured backlog, health fallback, `NoRouteError` — none
  of it exists there. All eight levels are ours or they are gone.
* **the quirk table as DATA.** LangChain solves `max_tokens_param` and Gemini's
  `thought_signature` *inside* `langchain_openai` / `langchain_google_genai` — so they are
  handled, but invisible and not overridable per agent or per model. `ctx_byte_budget`,
  `tool_output_max_chars`, `eviction_strategy` have no equivalent at all: `trim_messages` counts
  tokens and cannot evict tool payloads while keeping the dialogue.
* **cost in money, per agent and per task.** `usage_metadata` gives counts, not dollars, and
  nothing ties a call to a Linear id.
* **the overflow policy** — queue backlog and weekly budget are ours alone.
* **quality-failure escalation.** `with_fallbacks` fires on an *exception*. A call that succeeds
  and returns a bad answer is a success to LangChain. `NEEDS_CLARIFICATION` / `CAPABILITY_GAP`
  have no counterpart.
* **`guard.py` and OS-user impersonation** — outside LangChain's world entirely.

**And one collision that decides more than it looks:** every LangChain provider integration wants
the API key **in the process that builds the model**. Ruling 15 says the agent's container holds
no key. The two meet only if the secret bridge stops being a private protocol and becomes an
**OpenAI-compatible HTTP endpoint** — then a LangChain model points `base_url` at it with a dummy
key, and the real key never leaves the holder.

---

## 3. The one thing missing on both sides

The layered resolve has **no flow-step layer**. Its layers are engine → model → agent → contract.
What Yakov asked for — *"per flow step, which engine and which model"* — is a fifth layer, and it
is a small addition in either architecture. The `engine` column already added to the runtime's
`flow.csv` is the beginning of it and is still empty.

This does **not** break the iron rule. The rule forbids the *agent* from naming a model. A table
naming it IS the routing layer deciding.

---

## 4. Three architectures

### A — LangChain owns the engine layer

Install `langchain-openai` / `-google-genai` / `-xai`. Each node builds its model from the flow
row. `.with_fallbacks` gives the cascade. The secret bridge becomes an OpenAI-compatible proxy so
no key is in-process.

* **simplicity — best.** Our own transport, payload building and quirk handling all disappear.
* **functionality — the big loss.** All eight decision levels, the metering, the overflow policy,
  the quality escalation and the eviction strategy go. Rebuilding them on top means writing a
  router again — and then A has quietly become C.
* **reuse — none.** 2,470 lines in `engines/` become dead and must be deleted, not left.

### B — Ours owns everything, LangChain owns only the graph  *(what is running today)*

* **simplicity — good in isolation, false overall.** It is simple only because it currently does
  far less than the old layer.
* **functionality — the least of the three**, unless every one of §1's capabilities is rewritten.
* **reuse — zero, and this is the actual defect:** it leaves two routers, two engine tables, two
  key holders and two enforcement systems alive at once. This is the state we are in now.

### C — The old layer becomes the runtime's routing service

LangGraph nodes ask `engine_router.route()` for the decision and `engine_adapters.resolve()` for
the parameters; performing stays with `engine_core.run()` or a thin wrapper.

* **simplicity — worst.** A cross-tree dependency on 2,470 lines, and `engines/` assumes the
  host's Python and `/srv/golem` paths that the container will not have.
* **functionality — everything, on day one.**
* **reuse — maximum.**

### D — Split the decision from the execution  *(the recommendation)*

The **decision** is a pure function over tables and it is the irreplaceable part — perhaps 200
lines of real logic under those 684. Port it, with the tables, into the runtime; add the flow-step
layer as L4.

The **execution** — HTTP, payload shapes, provider quirks, retries — is what LangChain does better
than our `openrouter.py` ever will. Hand it over, and keep from the quirk table only the columns
LangChain does not cover (`ctx_byte_budget`, `tool_output_max_chars`, `eviction_strategy`,
`prompt_addendum`, `max_steps`).

The **boundary** is the secret bridge speaking OpenAI-compatible HTTP, so the key stays with the
holder and LangChain still works normally.

* **simplicity — second best**, and it is simple where it matters: one decision function, one
  transport we did not write.
* **functionality — keeps the eight precedence levels, the per-agent/per-model/per-engine layers
  and the metering; loses only the parts LangChain genuinely does better.**
* **reuse — the TABLES are reused, the CODE mostly is not.** Which is the right way round: the
  tables are the asset, the transport code is the liability.

---

## 5. What each alternative implies for the four live duplications

| duplicate pair | A | B | C | D |
| --- | --- | --- | --- | --- |
| two routers (`engine_router.py` / `engine.py`) | both die, LangChain replaces | both live — the defect | old wins, new dies | one survives: the ported decision |
| two engine tables (`agent_engines.csv` / `agents.csv`) | new wins | both live | old wins | **must merge — one table, with a flow-step layer** |
| two key holders (vault / `secrets/providers.json`) | bridge becomes a proxy; one holder | both live | old wins | bridge becomes a proxy; one holder |
| two enforcements (`enforcement.csv` / `run_gates.py`) | untouched by all four — a separate decision |

---

## 6. The question that has to be answered before any of this

Not "which library", but: **is `/srv/runtime` a second system, or the replacement for the first?**

If it is the replacement, every one of §5's rows resolves by deletion, and the rule that keeps it
honest already exists — `function_map.csv` carries a `to_delete` status, so a replacement can be
made to name its victims in the same commit.

If it is a second system, the duplications are permanent by design, and that should be said out
loud rather than discovered later.
