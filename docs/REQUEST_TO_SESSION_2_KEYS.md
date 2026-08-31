# Request to the GOL-291 session — vault access for the new runtime

**From:** the Interface, building `GOL-384` — Golem Runtime, phase A, branch `runtime-v3-langgraph`
**Date:** 2026-08-31 · **Relayed by Yakov** (the two sessions do not talk directly)

## What this is

A new runtime is being built at `/srv/runtime`, separate tree, separate repository, separate
OS user (`golem-runtime`, uid 974). It runs each agent in its own rootless container. Under
ruling 15 **the container holds no credential**: it asks a unix socket, and a bridge process
outside performs the call and returns the result.

That bridge is the only thing that needs secrets, and it is asking for the ones it cannot reach.

## What I am NOT asking for

* **Not secret values in a chat message.** A vault ACL grant is the right shape.
* **Not root, not sudo, not a new OS privilege.**
* **Not access to `/srv/golem`.** The two trees stay separate; this is about the vault service
  at `/run/golem-vault/vault.sock`, which is a service boundary and not shared code.

## Which principal should hold them

**Preferred: `golem-runtime` (uid 974, group `golem`).** That is the runtime's own user and the
one the bridge should run as. Today the bridge runs as `interface-lead` because that is the
principal the vault already answers; moving the grant to `golem-runtime` is the clean end state.

If granting to a new principal is expensive, `interface-lead` works and I will note it as debt.

## Already reachable — do not re-grant

`openai_key` · `gemini_key` · `google_api_key` · `google_cse_key` (now useless, see below) ·
`xai_key` · `mistral_key` · `elevenlabs_key` · `openrouter_key` · `token` (Telegram)

## The request, highest value first

| # | Secret | Why it is needed | Priority |
|---|---|---|---|
| 1 | `anthropic_key` — an Anthropic **API** key | **Every agent step currently fails its first route.** `agents.csv` routes every agent `anthropic/claude-opus-5` first, and the bridge holds no Anthropic credential, so all 31 agent steps of the design-robot flow cascade to OpenAI on every call. The Claude family is only reachable through the Max-plan CLI, which is `/srv/golem`-coupled and single-session — importing it would break the separation ruling 3 requires. **If no such key exists anywhere, say so and I will change the table instead of chasing it.** | **highest** |
| 2 | `gmail_oauth_client.json` + `gmail_token.json` (contents) | Steps 39 and 41 of the design-robot flow e-mail suppliers for missing STEP files. `gmail_send.py` in the old tree works and sends as `jake@golem-robotics.com`; the two token files are local to that tree and are not in the vault under any name I tried. | high (phase B by Yakov) |
| 3 | `linear_api_key` (or the OAuth app credential) | **14 of the 48 design-robot rows have `destination=linear`.** Right now those steps produce text that lands nowhere. Yakov has ruled this phase B, so this is a placement request, not an urgent one. | phase B |
| 4 | Digi-Key client id + secret; Mouser/Nexar key | Component price and availability, to enrich a BOM. Phase B by Yakov. | phase B |
| 5 | `langsmith_key` / `langchain_api_key` | The second UI. The local agent server reports `langsmith: false`, so the tracing screen — every call, tokens, cost — is dark. Ruling 7 says the subscription is not a topic and is there when needed; this is me saying it is now needed to see a run. **If no key exists, this is a purchase decision for Yakov, not a task for you.** | medium |

## One finding you should have, because it is a dead credential

`google_cse_key` and `google_cse_cx` are **valid and useless**. Measured today: the key returns
`200` on the YouTube API and `403 PERMISSION_DENIED` on Custom Search, and so does our other
Google key. Google has **closed the Custom Search JSON API to new customers**, existing ones
keep it only until **2027-01-01**, and since **2026-01-20** a new Programmable Search Engine
cannot search the whole web at all. There is no console setting that fixes it. If the old
system still lists Custom Search as an active tool, that row is stale.

## How to answer

A grant is enough — no reply text needed beyond "granted, under principal X". I will verify by
calling the vault and will report the result to Yakov.

If a secret does not exist at all, **saying so is the most useful answer**: it turns a missing
key into a decision instead of a search.
