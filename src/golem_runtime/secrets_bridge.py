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
import re
import socket
import socketserver
import threading
import time
import urllib.error
import urllib.parse
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


def _http_get(url: str, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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


def _call_xai(creds: dict[str, str], model: str, prompt: Any, system: str | None, timeout: float,
              tools: list[dict[str, Any]] | None = None, image: str | None = None) -> dict[str, Any]:
    """Grok, on the OpenAI-shaped chat-completions endpoint.

    It matters that this is a THIRD provider and not a second model of the same one: a
    second opinion from the same house is not a second opinion.
    """
    if tools or image or isinstance(prompt, list):
        raise LookupError("the xai path in phase A carries plain text only: no tools, no images, no multi-turn")
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    data = _http_json(
        "https://api.x.ai/v1/chat/completions",
        {"model": model, "messages": messages},
        {"Authorization": f"Bearer {creds['api_key']}"},
        timeout,
    )
    choice = (data.get("choices") or [{}])[0]
    usage = data.get("usage") or {}
    return {
        "text": (choice.get("message") or {}).get("content", ""),
        "usage": {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")},
        "provider_response_id": data.get("id"),
        "output": [],
    }


PERFORMERS = {"openai": _call_openai, "google": _call_google, "xai": _call_xai}


# --------------------------------------------------------------- non-model services
#
# A key-holding service that is NOT a model still belongs behind this bridge: the point
# of ruling 15 is that the container holds no credential, and that is as true of a search
# key as of a model key. `service` is the op; the agent asks, the holder performs.


def _svc_youtube_search(creds: dict[str, str], query: str, count: int = 6) -> dict[str, Any]:
    params = urllib.parse.urlencode({"key": creds["api_key"], "q": query, "part": "snippet",
                                     "type": "video", "maxResults": min(int(count), 25)})
    data = _http_get(f"https://www.googleapis.com/youtube/v3/search?{params}", timeout=60)
    return {"query": query, "results": [
        {"title": i["snippet"]["title"], "channel": i["snippet"]["channelTitle"],
         "published": i["snippet"]["publishedAt"],
         "url": f"https://www.youtube.com/watch?v={i['id']['videoId']}"}
        for i in data.get("items", []) if i.get("id", {}).get("videoId")
    ]}


def _svc_translate(creds: dict[str, str], text: str, target: str = "en") -> dict[str, Any]:
    data = _http_json(f"https://translation.googleapis.com/language/translate/v2?key={creds['api_key']}",
                      {"q": text, "target": target, "format": "text"}, {}, 60)
    translations = (data.get("data") or {}).get("translations") or [{}]
    return {"target": target, "translated": translations[0].get("translatedText", ""),
            "detected_source": translations[0].get("detectedSourceLanguage")}


def _svc_ocr(creds: dict[str, str], document_url: str) -> dict[str, Any]:
    """Mistral OCR: 170 languages including Hebrew, and it reads a scan, not just a PDF layer."""
    data = _http_json("https://api.mistral.ai/v1/ocr",
                      {"model": "mistral-ocr-latest",
                       "document": {"type": "document_url", "document_url": document_url}},
                      {"Authorization": f"Bearer {creds['api_key']}"}, 300)
    pages = data.get("pages") or []
    return {"document": document_url, "page_count": len(pages),
            "text": "\n\n".join(p.get("markdown", "") for p in pages)[:40000]}


def _svc_image_search(creds: dict[str, str], query: str, count: int = 8) -> dict[str, Any]:
    """Image search with no key at all.

    Measured 2026-08-31, and it is not a setting we can change: Google CLOSED the Custom
    Search JSON API to new customers, and both of our keys answer HTTP 403 PERMISSION_DENIED
    -- "this project does not have the access" -- while the very same key answers 200 on
    YouTube, so the key is fine and the entitlement is gone. Google also stopped letting new
    search engines search the entire web from 2026-01-20. There is nothing to enable.
    DuckDuckGo needs no key and is what the old system already uses for images.
    """
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) golem-runtime/0.1"}
    landing = urllib.request.Request(
        "https://duckduckgo.com/?" + urllib.parse.urlencode({"q": query, "iax": "images", "ia": "images"}),
        headers=headers)
    with urllib.request.urlopen(landing, timeout=60) as response:
        html = response.read().decode("utf-8", "replace")
    token = ""
    for pattern in (r'vqd="([\d-]+)"', r"vqd='([\d-]+)'", r"vqd=([\d-]+)&"):
        found = re.search(pattern, html)
        if found:
            token = found.group(1)
            break
    if not token:
        raise LookupError("no vqd token in the response - the image search front end changed")
    api = urllib.request.Request(
        "https://duckduckgo.com/i.js?" + urllib.parse.urlencode(
            {"l": "us-en", "o": "json", "q": query, "vqd": token, "f": ",,,", "p": "1"}),
        headers={**headers, "Referer": "https://duckduckgo.com/"})
    with urllib.request.urlopen(api, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    return {"query": query, "results": [
        {"title": r.get("title"), "image": r.get("image"), "context": r.get("url"),
         "width": r.get("width"), "height": r.get("height")}
        for r in (data.get("results") or [])[: int(count)]
    ]}


SERVICES = {
    "image_search": (None, _svc_image_search),
    "youtube_search": ("google_api", _svc_youtube_search),
    "translate": ("google_api", _svc_translate),
    "ocr": ("mistral", _svc_ocr),
}


def served_services() -> list[str]:
    """A service with no provider needs no credential, and is always served."""
    held = load_providers()
    return sorted(name for name, (provider, _) in SERVICES.items()
                  if provider is None or (held.get(provider) or {}).get("api_key"))


def perform_service(service: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if service not in SERVICES:
        raise LookupError(f"the bridge serves no service named {service!r}")
    provider, handler = SERVICES[service]
    if provider is None:
        return handler({}, **arguments)
    creds = load_providers().get(provider)
    if not creds or not creds.get("api_key"):
        raise LookupError(f"the bridge holds no credential for {provider!r}, which {service} needs")
    return handler(creds, **arguments)


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
            return {"ok": True, "version": PROTOCOL_VERSION, "providers": served_providers(), "services": served_services()}
        if op == "providers":
            return {"ok": True, "providers": served_providers(), "services": served_services()}
        if op == "service":
            try:
                return {"ok": True, **perform_service(request["service"], request.get("arguments") or {})}
            except urllib.error.HTTPError as exc:
                return {"ok": False, "error": f"HTTP {exc.code}: {exc.read().decode('utf-8','replace')[:300]}"}
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
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

    def _request(self, payload: dict[str, Any], timeout: float, attempts: int = 4) -> dict[str, Any]:
        """Retry the CONNECT, never the call.

        A bridge that is restarting refuses connections for a second or two. On 2026-08-31
        that was enough to end a 48-step run at step 29 with every route reporting
        "connection refused" -- the run died of a gap, not of a failure. Reconnecting is
        safe to retry because nothing has been sent yet; the request itself is not retried,
        because the far side may already have performed it.
        """
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._attempt(payload, timeout)
            except (ConnectionRefusedError, FileNotFoundError) as exc:
                last = exc
                time.sleep(2 * (attempt + 1))
        raise last if last else RuntimeError("the bridge could not be reached")

    def _attempt(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
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

    def service(self, service: str, arguments: dict[str, Any], timeout: float = 300.0) -> dict[str, Any]:
        return self._request({"op": "service", "service": service, "arguments": arguments}, timeout + 30)

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
