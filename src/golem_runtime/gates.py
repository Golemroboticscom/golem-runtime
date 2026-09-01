"""The gates: where the graph stops and a human decides.

Ruling 2: a gate stops the graph, the question goes to the chat, and the reply resumes the
run carrying the decision, the actor and the message id as provenance.

**A gate is not only approve-or-decline (Yakov #6620).** It has a SHAPE, and the shape is
read from the flow row's `must_answer` column -- a table, not code:

    (empty)                     approve   two buttons, as before
    choose: tracked | wheeled   choose    one button per option
    ask: what payload height?   ask       a real question, answered in words

And the message carries three separate things, in the order a person reads them:

    1. what happened   one line: who submitted, which step, how big
    2. the deliverable the files the step wrote, attached
    3. the question    short, and last, because it is what needs an answer

**An answer arrives three ways, and all three are anchored.** A button press carries the
gate in its callback data. A typed reply and a VOICE NOTE both have to be a Telegram reply
TO the gate's own message -- otherwise any chatter in the group could be read as a
decision. A voice note is transcribed by the runtime's own bridge, because the Interface
bridge is a different bot and never sees a reply addressed to this one (#6631).
"""
from __future__ import annotations

import base64
import html
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import tables
from .telegram import Telegram, as_telegram_html

HUMAN_DECISIONS = {"approve", "reject"}
EXTERNAL_DECISIONS = {"received", "cancel"}
DECISIONS_BY_KIND = {"human-gate": HUMAN_DECISIONS, "wait-external": EXTERNAL_DECISIONS}


class GateTimeout(RuntimeError):
    """Nobody answered inside `gate_timeout_minutes`. The run stays paused on disk."""


def parse_shape(must_answer: str) -> tuple[str, list[str], str]:
    """`must_answer` -> (shape, options, question). Empty means a plain approval."""
    text = (must_answer or "").strip()
    if not text:
        return "approve", [], ""
    head, _, rest = text.partition(":")
    head = head.strip().lower()
    if head == "choose":
        options = [o.strip() for o in rest.split("|") if o.strip()]
        return ("choose", options, "") if options else ("approve", [], "")
    if head in {"ask", "question"}:
        return "ask", [], rest.strip()
    return "approve", [], text


@dataclass
class GateRequest:
    run_id: str
    step: str
    kind: str
    actor: str
    action: str = ""
    output: str = ""
    error: str = ""
    deliverable: str = ""
    deliverable_step: str = ""
    deliverable_actor: str = ""
    deliverable_files: list[str] = field(default_factory=list)
    # The shape of the ask, from the flow row's `must_answer` column.
    shape: str = "approve"
    options: list[str] = field(default_factory=list)
    question: str = ""

    @property
    def decisions(self) -> set[str]:
        return DECISIONS_BY_KIND[self.kind]


def validate_answer(value: Any, kind: str, run_id: str) -> dict[str, str]:
    """An answer is only an answer if it says who decided and leaves a trail.

    A `choose` or an `ask` still resolves to one of the kind's decisions -- picking an
    option or writing a sentence IS an approval, carrying what was chosen or said.
    """
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
    for extra in ("chose", "said", "heard_language"):
        if value.get(extra):
            answer[extra] = str(value[extra])[:2000]
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
        value: dict[str, Any] = {
            "decision": decision,
            "actor": self.actor,
            "provenance": f"{self.provenance}:{request.step}",
        }
        if request.shape == "choose" and request.options and decision == "approve":
            value["chose"] = request.options[0]
        if request.shape == "ask" and decision == "approve":
            value["said"] = "auto-gate: no human was asked"
        return validate_answer(value, request.kind, request.run_id)

    def announce(self, run_id: str, text: str) -> None:
        pass


def _label(decision: str) -> str:
    return {"approve": "✅ approve", "reject": "⛔ reject", "received": "✅ received", "cancel": "⛔ cancel"}[decision]


class TelegramGate:
    """The real surface: what happened, the deliverable, then the question."""

    EXCERPT = 3200

    def __init__(self, telegram: Telegram | None = None, poll_seconds: int | None = None, timeout_minutes: int | None = None):
        self.telegram = telegram or Telegram()
        self.poll_seconds = int(poll_seconds if poll_seconds is not None else tables.control_int("gate_poll_seconds", "runtime"))
        self.timeout_minutes = int(
            timeout_minutes if timeout_minutes is not None else tables.control_int("gate_timeout_minutes", "runtime")
        )

    def announce(self, run_id: str, text: str) -> None:
        self.telegram.send(text)

    # ------------------------------------------------------------------ asking

    def _attach(self, request: GateRequest) -> int:
        """The files the step wrote ARE the deliverable; the answer text is a note."""
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
                self.telegram.send_document(document, f"Gate {request.step} · the step wrote no file; this is its answer in full")
        return sent

    def _buttons(self, request: GateRequest) -> list[list[dict[str, str]]] | None:
        if request.shape == "choose" and request.options:
            rows = [[{"text": f"{i + 1}. {o[:24]}", "callback_data": f"g|{request.step}|pick|{i}"[:64]}]
                    for i, o in enumerate(request.options[:8])]
            rows.append([{"text": _label("reject"), "callback_data": f"g|{request.step}|reject"[:64]}])
            return rows
        if request.shape == "ask":
            return None  # a question is answered in words, not with a button
        return [[{"text": _label(d), "callback_data": f"g|{request.step}|{d}"[:64]} for d in sorted(request.decisions)]]

    def _reading_copy(self, request: GateRequest) -> tuple[str, str]:
        """WHAT THE GATE SHOWS IS THE WORK, not the sentence the agent said about it.

        Measured on run carrier-2, gate 13: the step wrote a 6,148-byte register and then
        answered "I have written it to alternatives_rejected_step12.md" -- 126 characters.
        The gate quoted the 126 characters and headed them "126 chars", so what Yakov read
        was an empty gate over a full file, and he rejected it. The file was attached the
        whole time. A pointer is not a deliverable; the FILE is (#6637).
        """
        for name in request.deliverable_files:
            produced = Path(name)
            try:
                if produced.is_file() and produced.stat().st_size:
                    return produced.read_text(encoding="utf-8", errors="replace"), f"{produced.name} · {produced.stat().st_size:,} bytes"
            except OSError:
                continue
        return request.deliverable, f"{len(request.deliverable):,} chars"

    def _question(self, request: GateRequest) -> str:
        """Three parts, in reading order: what happened, the deliverable, the question."""
        lines: list[str] = []
        body, size = self._reading_copy(request)

        # 1. what happened
        if request.deliverable_actor:
            lines += [f"👤 <b>{html.escape(request.deliverable_actor)}</b> finished step "
                      f"{request.deliverable_step} · {size}", ""]

        # 2. the deliverable, as a reading copy
        if body:
            lines += ["<blockquote expandable>" + as_telegram_html(body[: self.EXCERPT]) + "</blockquote>"]
            if len(body) > self.EXCERPT:
                lines.append("<i>…cut here. The whole thing is attached above.</i>")
            lines.append("")
        else:
            lines += ["<i>(the step produced nothing)</i>", ""]

        # 3. the question, last and short
        ask = request.question or request.action or f"Gate {request.step}"
        lines.append(f"❓ <b>{html.escape(ask[:400])}</b>")
        if request.shape == "choose" and request.options:
            lines += [""] + [f"<b>{i + 1}.</b> {html.escape(o[:200])}" for i, o in enumerate(request.options[:8])]
        elif request.shape == "ask":
            lines.append("<i>Reply to THIS message — text or a voice note.</i>")
        if request.error:
            lines += ["", f"⚠ {html.escape(request.error)}"]
        lines += ["", f"<code>gate {request.step} · {request.kind} · run {request.run_id}</code>"]
        return "\n".join(lines)

    def ask(self, request: GateRequest) -> dict[str, str]:
        self._attach(request)
        message = self.telegram.send(self._question(request), self._buttons(request))
        self.message_id = message["message_id"]
        deadline = time.time() + self.timeout_minutes * 60
        while time.time() < deadline:
            try:
                updates = self.telegram.updates(self.poll_seconds)
            except Exception:
                time.sleep(5)
                continue
            for update in updates:
                answer = self._read(update, request)
                if answer is None:
                    continue
                closed = f"\n<b>{_label(answer['decision'])}</b> — {answer['actor']}"
                if answer.get("chose"):
                    closed += f"\n<b>chose:</b> {html.escape(answer['chose'][:200])}"
                if answer.get("said"):
                    closed += f"\n<b>said:</b> {html.escape(answer['said'][:400])}"
                self.telegram.edit(self.message_id, self._question(request) + closed)
                return answer
        raise GateTimeout(f"gate {request.run_id}|{request.step} unanswered after {self.timeout_minutes} minutes; the run stays paused")

    # ------------------------------------------------------------------ reading

    def _read(self, update: dict[str, Any], request: GateRequest) -> dict[str, str] | None:
        query = update.get("callback_query")
        if query:
            return self._read_button(query, request)
        return self._read_reply(update.get("message") or {}, request)

    def _read_button(self, query: dict[str, Any], request: GateRequest) -> dict[str, str] | None:
        parts = str(query.get("data", "")).split("|")
        user = query.get("from", {})
        actor = user.get("username") or user.get("first_name") or str(user.get("id", "unknown"))
        provenance = f"telegram:callback:{query['id']}:message:{query.get('message', {}).get('message_id')}"

        if len(parts) >= 3 and parts[0] == "g" and parts[1] == request.step:
            if parts[2] == "pick" and request.shape == "choose":
                index = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else -1
                if 0 <= index < len(request.options):
                    self.telegram.answer_callback(query["id"], f"chose: {request.options[index][:60]}")
                    return validate_answer(
                        {"decision": "approve", "actor": actor, "provenance": provenance, "chose": request.options[index]},
                        request.kind, request.run_id,
                    )
            elif parts[2] in request.decisions:
                self.telegram.answer_callback(query["id"], f"{request.step}: {parts[2]}")
                return validate_answer(
                    {"decision": parts[2], "actor": actor, "provenance": provenance}, request.kind, request.run_id
                )
        # A press that belongs to no open gate must SAY SO. Silence leaves the button
        # spinning and the person believing they answered (#6610).
        self.telegram.answer_callback(
            query["id"], f"That button is not the open gate. The live one is gate {request.step} of run {request.run_id}."
        )
        return None

    def _read_reply(self, message: dict[str, Any], request: GateRequest) -> dict[str, str] | None:
        """A typed or spoken answer, and it MUST be a reply to this gate's own message.

        Without that anchor any sentence in the group could be read as a decision.
        """
        if str(message.get("chat", {}).get("id")) != self.telegram.chat_id:
            return None
        replied = (message.get("reply_to_message") or {}).get("message_id")
        if replied != getattr(self, "message_id", None):
            return None

        user = message.get("from", {})
        actor = user.get("username") or user.get("first_name") or str(user.get("id", "unknown"))
        provenance = f"telegram:message:{message.get('message_id')}"
        language = ""

        text = str(message.get("text") or message.get("caption") or "").strip()
        media = message.get("voice") or message.get("audio") or message.get("video_note")
        if not text and media:
            heard = self._transcribe(media)
            if heard is None:
                return None
            text, language = heard
            provenance += ":voice"
        if not text:
            return None

        return self._interpret(text, actor, provenance, language, request)

    def _transcribe(self, media: dict[str, Any]) -> tuple[str, str] | None:
        """The gate's own ears. The Interface bridge is a different bot and never sees this."""
        from .secrets_bridge import BridgeClient

        try:
            audio, filename = self.telegram.download(media["file_id"])
            answer = BridgeClient().service(
                "transcribe", {"audio_base64": base64.b64encode(audio).decode("ascii"), "filename": filename}
            )
        except Exception:
            return None
        if not answer.get("ok") or not answer.get("text"):
            return None
        return answer["text"].strip(), str(answer.get("language") or "")

    def _interpret(self, text: str, actor: str, provenance: str, language: str, request: GateRequest) -> dict[str, str] | None:
        """Turn what a person wrote or said into this gate's decision."""
        lowered = text.strip().lower()
        first = lowered.split()[0] if lowered.split() else ""
        yes = {"approve", "ok", "okay", "yes", "אשר", "כן", "מאשר", "מאושר", "received", "התקבל"}
        no = {"reject", "no", "דחה", "לא", "cancel", "בטל"}

        if request.shape == "choose" and request.options:
            if first.rstrip(".").isdigit():
                index = int(first.rstrip(".")) - 1
                if 0 <= index < len(request.options):
                    return validate_answer(
                        {"decision": "approve", "actor": actor, "provenance": provenance,
                         "chose": request.options[index], "said": text, "heard_language": language},
                        request.kind, request.run_id)
            for option in request.options:
                if option.lower() in lowered:
                    return validate_answer(
                        {"decision": "approve", "actor": actor, "provenance": provenance,
                         "chose": option, "said": text, "heard_language": language},
                        request.kind, request.run_id)

        if request.shape == "ask":
            # Anything said IS the answer; only an explicit refusal stops the run.
            decision = "reject" if first in no else ("approve" if request.kind == "human-gate" else "received")
            return validate_answer(
                {"decision": decision, "actor": actor, "provenance": provenance, "said": text, "heard_language": language},
                request.kind, request.run_id)

        if first in yes:
            decision = "approve" if request.kind == "human-gate" else "received"
        elif first in no:
            decision = "reject" if request.kind == "human-gate" else "cancel"
        else:
            return None
        return validate_answer(
            {"decision": decision, "actor": actor, "provenance": provenance, "said": text, "heard_language": language},
            request.kind, request.run_id)
