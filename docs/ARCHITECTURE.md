# The architecture: four layers, each held by whoever can actually enforce it

Yakov, #6781: put this in the architecture part.

The question behind it was his (#6777): *how can things this basic not be expressed natively
inside the largest library for managing AI flows?* The answer is not that LangGraph is missing
something. It is that **a library runs inside your process, and mounts, containers, users,
networks and secrets are properties of the process and the machine.** A library cannot enforce
what it lives inside: whatever it "enforced", the code calling it could undo in one line.

**A permission enforced by the same process it constrains is not a permission.**

So the work splits four ways, and each part sits with whoever can hold it:

| layer | what it answers | who holds it | where, here |
| --- | --- | --- | --- |
| **orchestration** | which step, in what order, carrying what state | the library | LangGraph — `compiler.py`, `runner.py` |
| **identity and policy** | who the actor is, what it is ALLOWED to touch | a declaration outside the code | `tables/agents.csv`, `tables/flow.csv` |
| **isolation** | what it is ABLE to touch | the operating system | rootless podman — `containers.py` |
| **credentials** | who holds the keys, and who may ask | a separate holder the work calls | the secret bridge — `secrets_bridge.py` |

This is the same shape a Pod specification has: the manifest declares image, volumes, network,
secrets and identity; a different component enforces it; and the application code knows nothing
about any of it. A row of `agents.csv` is that manifest, `containers.py` is that enforcer.

**The consequence that decides things.** Because isolation belongs to the machine, it does not
survive moving the machine. That is why LangGraph Platform fails Yakov's permissions criterion
(#6730): the rootless container per agent and the per-agent mount list are not imported from
anywhere — they are built AROUND the process. Where the process is not ours, there is nothing to
build around.

**What LangGraph does instead.** It assumes the process it runs in is already trusted. Its only
notion of a boundary is a tool you wrote yourself: if you put a check in it, there is a check;
if you did not, there is none.

---

## The boundary between the bridge and the gate

The same principle, applied to the newest component (#6640). The Interface bridge and the
runtime's gate are **the same kind of thing — a channel adapter — and they are siblings, not
layers.** What they share sits BELOW them, never in each other.

* **Anything belonging to a conversation** — a bot token, a poller, an offset, who is being
  spoken to — is owned by exactly one adapter and is never shared. Telegram forces this: two
  pollers on one token consume each other's updates.
* **Anything that is a capability** — transcribe audio, speak text, render a PDF — belongs in a
  shared library below both, and is duplicated in neither.

The test, when the runtime needs something the bridge already has: *neither add it to the bridge
nor copy it.* If it is a capability, move it down. If it is a conversation, it belongs to the bot
that holds that conversation.

Measured example: transcription is already a capability, not a bridge feature. It lives in
`agents/interface/voice.py`, imports only the standard library, and is registered as shared with
every agent — the bridge merely calls it.

---

## Where the runtime belongs in the existing map

`_meta/component_map.csv` has 17 rows and 8 columns and is the system's component architecture.
The runtime is **row 18** — not a second architecture document. Its code living in another
repository is no obstacle: the `CI gates` row already describes code in `golem-tooling` and says
so explicitly.

`_meta/function_map.csv` carries a `status` per function including `to_delete`. That is the
mechanism that keeps a replacement honest: **a replacement names its victims in the same commit**,
so the transition has a countable end instead of an implied one (GOL-385).
