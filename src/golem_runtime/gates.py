"""The gates: where the graph stops and a human decides.

Ruling 2: a gate stops the graph, the question goes to the chat, and the reply resumes the
run carrying the decision, the actor and the message id as provenance. The design-robot
flow has twelve human gates and two external waits (ruling 17), so this is load-bearing
from the first real run rather than an extra bolted on later.

A gate channel is an interface with two implementations: `AutoGate` for the suite and dry
runs, `TelegramGate` for real work. The graph knows neither.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import tables
from .telegram import Telegram

HUMAN_DECISIONS = {"approve", "reject"}
EXTERNAL_DECISIONS = {"received", "cancel"}
DECISIONS_BY_KIND = {"human-gate": HUMAN_DECISIONS, "wait-external": EXTERNAL_DECISIONS}


class GateTimeout(RuntimeError):
    """Nobody answered inside `gate_timeout_minutes`. The run stays paused on disk."""


@dataclass
class GateRequest:
    run_id: str
    step: str
    kind: str
    actor: str
    action: str = ""
    output: str = ""
    error: str = ""
    # WHAT is being approved. A gate without it asks a human to decide about nothing --
    # Yakov pressed approve on "Approve the mission spec" with no spec attached (#6598).
    deliverable: str = ""
    deliverable_step: str = ""
    deliverable_actor: str = ""
    deliverable_files: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def decisions(self) -> set[str]:
        return DECISIONS_BY_KIND[self.kind]


def validate_answer(value: Any, kind: str, run_id: str) -> dict[str, str]:
    """An answer is only an answer if it says who decided and leaves a trail."""
    if not isinstance(value, dict):
        raise ValueError("resume input must be an object")
    required = ("decision", "actor", "provenance")
    missing = [k for k in required if not isinstance(value.get(k), str) or not value[k].strip()]
    if missing:
        raise ValueError(f"resume input requires non-empty {list(required)}; missing {missing}")
    decision = value["decision"].strip()
    if decision not in DECISIONS_BY_KIND[kind]:
        raise ValueError(f"invalid {kind} decision {decision!r}; expected one of {sorted(DECISIONS_BY_KIND[kind])}")
    answer = {k: value[k].strip() for k in required}
    answer["run_id"] = run_id
    if value.get("note"):
        answer["note"] = str(value["note"])[:500]
    return answer


class GateChannel(Protocol):
    def ask(self, request: GateRequest) -> dict[str, str]:
        ...

    def announce(self, run_id: str, text: str) -> None:
        ...


class AutoGate:
    """Answers itself. The suite and every dry run use this; real work never does."""

    def __init__(self, decisions: dict[str, str] | None = None, actor: str = "auto", provenance: str = "auto-gate"):
        self.decisions = decisions or {}
        self.actor = actor
        self.provenance = provenance
        self.asked: list[GateRequest] = []

    def ask(self, request: GateRequest) -> dict[str, str]:
        self.asked.append(request)
        default = "approve" if request.kind == "human-gate" else "received"
        decision = self.decisions.get(request.step, default)
        return validate_answer(
            {"decision": decision, "actor": self.actor, "provenance": f"{self.provenance}:{request.step}"},
            request.kind,
            request.run_id,
        )

    def announce(self, run_id: str, text: str) -> None:
        pass


def _label(decision: str) -> str:
    return {"approve": "✅ approve", "reject": "⛔ reject", "received": "✅ received", "cancel": "⛔ cancel"}[decision]


class TelegramGate:
    """The real surface. One message per gate, two buttons, and the answer carries the id."""

    def __init__(self, telegram: Telegram | None = None, poll_seconds: int | None = None, timeout_minutes: int | None = None):
        self.telegram = telegram or Telegram()
        self.poll_seconds = int(poll_seconds if poll_seconds is not None else tables.control_int("gate_poll_seconds", "runtime"))
        self.timeout_minutes = int(
            timeout_minutes if timeout_minutes is not None else tables.control_int("gate_timeout_minutes", "runtime")
        )

    def announce(self, run_id: str, text: str) -> None:
        self.telegram.send(text)

    def ask(self, request: GateRequest) -> dict[str, str]:
        token = f"{request.run_id}|{request.step}"
        # The whole deliverable travels as a file when it does not fit in a message. A gate
        # that shows only a headline is a gate that asks for a signature on an unread page.
        # THE FILES THE STEP WROTE are the deliverable. The answer text is a covering note,
        # and when the agent writes a file that note is just a pointer -- attaching it sent
        # Yakov 108 bytes containing a link while 15 KB of research sat on disk (#6628).
        sent = 0
        for name in request.deliverable_files[:5]:
            produced = Path(name)
            if produced.is_file() and produced.stat().st_size:
                self.telegram.send_document(
                    produced,
                    f"Gate {request.step} · {produced.name} · {produced.stat().st_size:,} bytes "
                    f"· written by {request.deliverable_actor or 'unknown'} at step {request.deliverable_step}",
                )
                sent += 1
        if not sent and request.deliverable:
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                document = Path(tmp) / f"step-{request.deliverable_step}-{(request.deliverable_actor or 'agent').replace(' ', '-')}.md"
                document.write_text(request.deliverable, encoding="utf-8")
                self.telegram.send_document(
                    document,
                    f"Gate {request.step} · the step wrote no file; this is its answer in full",
                )
        buttons = [[{"text": _label(d), "callback_data": f"g|{request.step}|{d}"[:64]} for d in sorted(request.decisions)]]
        message = self.telegram.send(self._question(request), buttons)
        message_id = message["message_id"]
        deadline = time.time() + self.timeout_minutes * 60
        while time.time() < deadline:
            try:
                updates = self.telegram.updates(self.poll_seconds)
            except Exception:
                # The gate waits. Nothing that happens to the network ends the run here.
                time.sleep(5)
                continue
            for update in updates:
                answer = self._read(update, request)
                if answer is None:
                    continue
                # The button's spinner clears in `_read`, before this edit, because the edit is
                # a second round trip and the spinner is what Yakov actually watches.
                self.telegram.edit(message_id, self._question(request) + f"\n<b>{_label(answer['decision'])}</b> — {answer['actor']}")
                return answer
        raise GateTimeout(f"gate {token} unanswered after {self.timeout_minutes} minutes; the run stays paused")

    # An expandable quote holds far more than a code block did, and Telegram collapses it
    # itself with a "show more", so a long deliverable stays readable instead of flooding.
    EXCERPT = 3200

    def _question(self, request: GateRequest) -> str:
        """The question, with the RAW deliverable and the name of who submitted it.

        Raw on purpose (#6600): no summary, no rewrite. What the agent produced is what
        Yakov judges. Anything else puts a second author between them.
        """
        import html

        from .telegram import as_telegram_html

        # NOT <pre>. Telegram draws a code block with a TRANSLUCENT background, so the chat
        # wallpaper shows through it as a bright band down the middle -- which is the white
        # stripe Yakov photographed (#6605), and it appeared only in these messages because
        # they were the only ones using a code block. An expandable blockquote is opaque,
        # uses the normal proportional font, and collapses itself when long.
        head = f"<b>{html.escape(request.action[:400])}</b>" if request.action else f"<b>Gate {request.step}</b>"
        lines = [head, ""]
        if request.deliverable:
            lines += [
                f"👤 <b>{html.escape(request.deliverable_actor or 'unknown')}</b>  ·  "
                f"step {request.deliverable_step}"
                + (f"  ·  {len(request.deliverable_files)} file(s) attached"
                   if request.deliverable_files else f"  ·  {len(request.deliverable):,} chars"),
                "",
                # Rendered, not dumped: the markdown becomes real bold and real bullets.
                # The attached file above is still the untouched raw text.
                "<blockquote expandable>" + as_telegram_html(request.deliverable[: self.EXCERPT]) + "</blockquote>",
            ]
            if len(request.deliverable) > self.EXCERPT:
                lines.append("<i>…cut here. The whole raw text is the file above.</i>")
        else:
            lines += ["<i>(no deliverable was produced before this gate)</i>"]
        if request.error:
            lines += ["", f"⚠ {html.escape(request.error)}"]
        lines += ["", f"<code>gate {request.step} · {request.kind} · run {request.run_id}</code>"]
        return "\n".join(lines)

    def _read(self, update: dict[str, Any], request: GateRequest) -> dict[str, str] | None:
        query = update.get("callback_query")
        if query:
            parts = str(query.get("data", "")).split("|")
            matches = len(parts) == 3 and parts[0] == "g" and parts[1] == request.step
            decision = parts[2] if len(parts) == 3 else ""
            if not matches or decision not in request.decisions:
                # A press that belongs to no open gate must SAY SO. Silence leaves the
                # button spinning and the person believing they answered -- which is what
                # happened on 2026-09-01 when Yakov pressed a layout sample I had sent with
                # live-looking buttons, and nothing moved for forty minutes (#6610).
                self.telegram.answer_callback(
                    query["id"],
                    f"That button is not the open gate. The live one is gate {request.step} of run {request.run_id}.",
                )
                return None
            user = query.get("from", {})
            actor = user.get("username") or user.get("first_name") or str(user.get("id", "unknown"))
            self.telegram.answer_callback(query["id"], f"{request.step}: {decision}")
            return validate_answer(
                {
                    "decision": decision,
                    "actor": actor,
                    "provenance": f"telegram:callback:{query['id']}:message:{query.get('message', {}).get('message_id')}",
                },
                request.kind,
                request.run_id,
            )
        message = update.get("message") or {}
        text = str(message.get("text", "")).strip().lower()
        if not text or str(message.get("chat", {}).get("id")) != self.telegram.chat_id:
            return None
        word = text.split()[0]
        synonyms = {
            "approve": "approve", "ok": "approve", "yes": "approve", "אשר": "approve", "כן": "approve",
            "reject": "reject", "no": "reject", "דחה": "reject", "לא": "reject",
            "received": "received", "התקבל": "received",
            "cancel": "cancel", "בטל": "cancel",
        }
        decision = synonyms.get(word)
        if decision not in request.decisions:
            return None
        user = message.get("from", {})
        actor = user.get("username") or user.get("first_name") or str(user.get("id", "unknown"))
        return validate_answer(
            {"decision": decision, "actor": actor, "provenance": f"telegram:message:{message.get('message_id')}"},
            request.kind,
            request.run_id,
        )
