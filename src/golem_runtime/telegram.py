"""Telegram, held by the runtime itself.

Ruling 2: the approval surface is Telegram, and in phase A it is Yakov's existing chat.
Shared channel, not shared code -- this module holds the runtime's own token and chat id
in `secrets/telegram.json` and imports nothing from /srv/golem.

One measured constraint shapes it. The Interface bridge long-polls its bot token without
pause, and Telegram's getUpdates offset deletes every update below it, so a second poller
on the SAME token would eat Yakov's messages to the Interface. The runtime therefore
speaks as a separate live bot in the same group. That is a value in the secrets file, not
a fact in the code: swap `bot_token` and nothing else moves.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .paths import SECRETS_DIR

TELEGRAM_JSON = SECRETS_DIR / "telegram.json"
API = "https://api.telegram.org"


class TelegramNotConfigured(RuntimeError):
    pass


def load_config(path: Path | None = None) -> dict[str, str]:
    path = Path(path or TELEGRAM_JSON)
    if not path.exists():
        raise TelegramNotConfigured(f"no {path}; the runtime needs its own token and chat id")
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("bot_token", "chat_id"):
        if not config.get(key):
            raise TelegramNotConfigured(f"{path} has no {key}")
    return config


def as_telegram_html(markdown: str) -> str:
    """Render an agent's markdown the way Telegram can actually show it.

    The deliverables come back as markdown -- `# heading`, `**bold**`, `- item`. Dropped
    into a message unrendered, Yakov sees the asterisks and hashes themselves, which is
    what he flagged (#6618): "no bold and it is not comfortable to read; it should look
    like the messages you send me." My own messages read well because they are HTML.

    The RAW text is not lost: the attached file is untouched. This is the reading copy.
    """
    out = html.escape(markdown)
    # [ \t] and never \s: in MULTILINE, \s matches the newline itself, so a heading
    # pattern anchored with \s*$ swallows the blank line after it and welds two headings
    # together. Caught on the first render.
    out = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*$", r"<b>\1</b>", out, flags=re.M)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out, flags=re.S)                # **bold**
    out = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", out)  # *italic*
    out = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", out)                        # `code`
    out = re.sub(r"^[ \t]{0,4}[-*+][ \t]+", "• ", out, flags=re.M)                 # bullets
    out = re.sub(r"^[ \t]*[-*_]{3,}[ \t]*$", "──────────", out, flags=re.M)       # rules
    out = re.sub(r"\n{3,}", "\n\n", out)                                          # breathing room
    return out.strip()


class Telegram:
    def __init__(self, config: dict[str, str] | None = None, timeout: float = 40.0):
        self.config = config or load_config()
        self.token = self.config["bot_token"]
        self.chat_id = str(self.config["chat_id"])
        self.timeout = timeout
        self._offset: int | None = None

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        body = urllib.parse.urlencode(
            {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in (params or {}).items()}
        ).encode("utf-8")
        request = urllib.request.Request(f"{API}/bot{self.token}/{method}", data=body)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8", "replace"))
            except ValueError:
                return {"ok": False, "description": f"HTTP {exc.code}"}
        except Exception as exc:
            # EVERY transport failure is an ANSWER, never an exception that escapes.
            #
            # Measured on 2026-08-31: a run sat at gate 22 for an hour of healthy 25-second
            # long-polls, then one `getUpdates` read timed out -- and the TimeoutError came
            # straight up through the gate and killed the run. A gate is supposed to wait a
            # day for a human; a single network hiccup must not be what ends it.
            return {"ok": False, "description": f"{type(exc).__name__}: {exc}"}

    def me(self) -> dict[str, Any]:
        return self.call("getMe", timeout=15)

    LIMIT = 4000

    @staticmethod
    def fit(text: str, limit: int = 4000) -> str:
        """Cut to length WITHOUT orphaning an HTML tag.

        `text[:4000]` is not a safe cut: on 2026-09-01 it landed inside a gate's
        `<blockquote>`, the closing tag went over the edge, and Telegram answered "can't
        find end tag corresponding to start tag blockquote" -- four times, which killed a
        live run at step 28. A truncation that produces an unsendable message is worse than
        a short one, so anything still open when the knife falls is closed here.
        """
        if len(text) <= limit:
            return text
        cut = text[:limit]
        open_tags: list[str] = []
        for match in re.finditer(r"<(/?)([a-zA-Z]+)[^>]*>", cut):
            closing, name = match.group(1), match.group(2).lower()
            if closing:
                if open_tags and open_tags[-1] == name:
                    open_tags.pop()
            else:
                open_tags.append(name)
        tail = "".join(f"</{name}>" for name in reversed(open_tags))
        # A tag cannot be half-written either: drop a trailing "<b" with no ">".
        last_open = cut.rfind("<")
        if last_open > cut.rfind(">"):
            cut = cut[:last_open]
        return cut + tail

    def send(self, text: str, buttons: list[list[dict[str, str]]] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": self.fit(text, self.LIMIT),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        for attempt in range(4):
            answer = self.call("sendMessage", params)
            if answer.get("ok"):
                return answer["result"]
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"telegram refused the send four times: {answer}")

    def send_document(self, path: Path, caption: str = "") -> dict[str, Any] | None:
        """Send a file. Used when a gate's deliverable is too long to read in a message."""
        path = Path(path)
        boundary = "----golem-runtime-boundary"
        parts: list[bytes] = []
        for key, value in (("chat_id", self.chat_id), ("caption", caption[:1000])):
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8")
            )
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: text/markdown\r\n\r\n".encode("utf-8")
        )
        parts.append(path.read_bytes())
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        request = urllib.request.Request(
            f"{API}/bot{self.token}/sendDocument",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return None

    def edit(self, message_id: int, text: str) -> None:
        self.call(
            "editMessageText",
            {"chat_id": self.chat_id, "message_id": message_id, "text": text[:4000], "parse_mode": "HTML"},
        )

    def answer_callback(self, callback_id: str, text: str) -> None:
        self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]})

    def download(self, file_id: str) -> tuple[bytes, str]:
        """Fetch a Telegram file's bytes. Used for a voice note answering a gate."""
        answer = self.call("getFile", {"file_id": file_id}, timeout=30)
        if not answer.get("ok"):
            raise RuntimeError(f"telegram would not describe the file: {answer}")
        path = answer["result"]["file_path"]
        with urllib.request.urlopen(f"{API}/file/bot{self.token}/{path}", timeout=180) as response:
            return response.read(), path.rsplit("/", 1)[-1]

    def updates(self, wait_seconds: int) -> list[dict[str, Any]]:
        # Only the two kinds a gate can be answered with. Anything else is noise that would
        # be fetched, ignored, and would still consume the offset.
        params: dict[str, Any] = {"timeout": wait_seconds, "allowed_updates": ["callback_query", "message"]}
        if self._offset is not None:
            params["offset"] = self._offset
        answer = self.call("getUpdates", params, timeout=wait_seconds + 20)
        if not answer.get("ok"):
            time.sleep(2)
            return []
        results = answer.get("result", [])
        if results:
            self._offset = results[-1]["update_id"] + 1
        return results

    def drain(self) -> None:
        """Discard anything queued before this run started listening."""
        self.updates(0)
