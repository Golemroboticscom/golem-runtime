"""The secret bridge — the container holds no key.

Ruling 15: in phase A the container's secrets arrive through a BRIDGE. The agent process
asks a unix socket, and the holder outside performs the provider call and returns the
result. The key never enters the container, never enters the graph state and never enters
a record.

The direction matters: bridge -> secret-inside is an addition later; secret-inside ->
bridge would be a rewrite. So the bridge is what phase A builds.

Server side runs on the host as the credential holder. Client side is what the engine
wrapper talks to, and it is the only thing that ships into the container.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import socket
import socketserver
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .paths import SECRETS_DIR, SECRET_SOCKET

PROVIDERS_FILE = SECRETS_DIR / "providers.json"
PROTOCOL_VERSION = 1


# --------------------------------------------------------------------------- providers


def load_providers() -> dict[str, dict[str, str]]:
    """Provider credentials, held only by the bridge process."""
    if not PROVIDERS_FILE.exists():
        return {}
    with PROVIDERS_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


def _http_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _image_part(image: str) -> dict[str, Any]:
    """A URL travels as a URL; a file on the host travels as inline data.

    The bridge is the only process that touches the file, and it is already the
    process that holds every credential -- so nothing new crosses the boundary.
    """
    if image.startswith(("http://", "https://")):
        return {"type": "input_image", "image_url": image}
    path = Path(image[len("file://") :] if image.startswith("file://") else image)
    media = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{media};base64,{encoded}"}


def _as_input(prompt: Any, image: str | None) -> Any:
    """A string becomes one user turn; a list is already a conversation and travels whole."""
    if isinstance(prompt, list):
        return prompt
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    if image:
        content.append(_image_part(image))
    return [{"role": "user", "content": content}]


def _call_openai(creds: dict[str, str], model: str, prompt: Any, system: str | None, timeout: float,
                 tools: list[dict[str, Any]] | None = None, image: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": model, "input": _as_input(prompt, image)}
    if system:
        payload["instructions"] = system
    if tools:
        payload["tools"] = tools
    data = _http_json(
        "https://api.openai.com/v1/responses",
        payload,
        {"Authorization": f"Bearer {creds['api_key']}"},
        timeout,
    )
    chunks: list[str] = []
    for item in data.get("output", []):
        for part in item.get("content", []) or []:
            if part.get("type") == "output_text":
                chunks.append(part.get("text", ""))
    usage = data.get("usage") or {}
    return {
        "text": "".join(chunks),
        "usage": {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")},
        "provider_response_id": data.get("id"),
        "output": data.get("output", []),
    }


def _call_google(creds: dict[str, str], model: str, prompt: Any, system: str | None, timeout: float,
                 tools: list[dict[str, Any]] | None = None, image: str | None = None) -> dict[str, Any]:
    if tools or image or isinstance(prompt, list):
        # Honest refusal rather than a silent downgrade: the caller cascades to a route
        # that can do the job, and the record says why this one could not.
        raise LookupError("the google path in phase A carries plain text only: no tools, no images, no multi-turn")
    name = model if model.startswith("models/") else f"models/{model}"
    payload: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    data = _http_json(
        f"https://generativelanguage.googleapis.com/v1beta/{name}:generateContent?key={creds['api_key']}",
        payload,
        {},
        timeout,
    )
    chunks: list[str] = []
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []) or []:
            if "text" in part:
                chunks.append(part["text"])
    usage = data.get("usageMetadata") or {}
    return {
        "text": "".join(chunks),
        "usage": {"input_tokens": usage.get("promptTokenCount"), "output_tokens": usage.get("candidatesTokenCount")},
        "provider_response_id": data.get("responseId"),
        "output": [],
    }


PERFORMERS = {"openai": _call_openai, "google": _call_google}


def served_providers() -> list[str]:
    """Which providers this bridge can actually perform a call for, right now."""
    return sorted(p for p, creds in load_providers().items() if p in PERFORMERS and creds.get("api_key"))


def perform(provider: str, model: str, prompt: Any, system: str | None = None, timeout: float = 600.0,
            tools: list[dict[str, Any]] | None = None, image: str | None = None) -> dict[str, Any]:
    creds = load_providers().get(provider)
    if not creds or not creds.get("api_key"):
        raise LookupError(f"the bridge holds no credential for provider {provider!r}")
    if provider not in PERFORMERS:
        raise LookupError(f"the bridge cannot perform calls for provider {provider!r} in phase A")
    return PERFORMERS[provider](creds, model, prompt, system, timeout, tools, image)


# ------------------------------------------------------------------------------ server


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        for raw in self.rfile:
            if not raw.strip():
                continue
            try:
                request = json.loads(raw)
            except ValueError as exc:
                self._reply({"ok": False, "error": f"malformed request: {exc}"})
                continue
            self._reply(self._dispatch(request))

    def _reply(self, payload: dict[str, Any]) -> None:
        self.wfile.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        op = request.get("op")
        if op == "ping":
            return {"ok": True, "version": PROTOCOL_VERSION, "providers": served_providers()}
        if op == "providers":
            return {"ok": True, "providers": served_providers()}
        if op != "complete":
            return {"ok": False, "error": f"unknown op {op!r}"}
        try:
            result = perform(
                request["provider"],
                request["model"],
                request["prompt"],
                request.get("system"),
                float(request.get("timeout", 600)),
                request.get("tools"),
                request.get("image"),
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            return {"ok": False, "error": f"HTTP {exc.code}: {detail}", "status": exc.code}
        except Exception as exc:  # the bridge answers, it never dies on one bad call
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, **result}


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(socket_path: Path | None = None) -> _Server:
    """Start the bridge in a background thread and return the server."""
    path = Path(socket_path or SECRET_SOCKET)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    server = _Server(str(path), _Handler)
    os.chmod(path, 0o660)
    threading.Thread(target=server.serve_forever, daemon=True, name="secret-bridge").start()
    return server


# ------------------------------------------------------------------------------ client


class BridgeClient:
    """What runs inside the container. It carries an address, never a key."""

    def __init__(self, socket_path: Path | None = None):
        self.socket_path = Path(socket_path or SECRET_SOCKET)

    def _request(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(self.socket_path))
            sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            buffer = b""
            while not buffer.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
        return json.loads(buffer.decode("utf-8"))

    def ping(self, timeout: float = 10.0) -> dict[str, Any]:
        return self._request({"op": "ping"}, timeout)

    def providers(self, timeout: float = 10.0) -> list[str]:
        return list(self._request({"op": "providers"}, timeout).get("providers", []))

    def complete(self, provider: str, model: str, prompt: Any, system: str | None, timeout: float,
                 tools: list[dict[str, Any]] | None = None, image: str | None = None) -> dict[str, Any]:
        return self._request(
            {"op": "complete", "provider": provider, "model": model, "prompt": prompt, "system": system,
             "timeout": timeout, "tools": tools, "image": image},
            timeout + 30,
        )


def main() -> None:
    import argparse
    import time

    parser = argparse.ArgumentParser(description="run the secret bridge in the foreground")
    parser.add_argument("--socket", type=Path, default=SECRET_SOCKET)
    args = parser.parse_args()
    serve(args.socket)
    print(f"secret bridge on {args.socket}, serving {served_providers()}", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
