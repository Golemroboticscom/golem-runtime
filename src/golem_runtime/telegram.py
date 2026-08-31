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

import json
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

    def send(self, text: str, buttons: list[list[dict[str, str]]] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text[:4000],
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

    def edit(self, message_id: int, text: str) -> None:
        self.call(
            "editMessageText",
            {"chat_id": self.chat_id, "message_id": message_id, "text": text[:4000], "parse_mode": "HTML"},
        )

    def answer_callback(self, callback_id: str, text: str) -> None:
        self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]})

    def updates(self, wait_seconds: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": wait_seconds}
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
