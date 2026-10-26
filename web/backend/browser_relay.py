"""Relais navigateur pour joindre un NemApi distant depuis un backend cloisonné.

Le backend hébergé dans un environnement à sortie réseau restreinte ne peut pas
ouvrir de connexion vers un tunnel (ex. trycloudflare.com) : le handshake TLS
est coupé (``SSLZeroReturnError``). Le navigateur de l'utilisateur, lui, peut
atteindre ce tunnel. Ce module sérialise donc les appels HTTP du provider
NEMAPI sur un WebSocket ``/ws/relay`` ; le navigateur exécute le ``fetch()``
(NemApi autorise les origines croisées) et renvoie la réponse.

Le relais est optionnel et activé par ``nemapi.browser_relay`` (uniquement
interprété par le backend Web : le CLI et Telegram gardent leurs requêtes
directes).
"""
from __future__ import annotations

import asyncio
import json as jsonlib
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

RELAY_CONNECT_GRACE = 6.0
RELAY_MAX_WAIT = 900.0


class RelayError(RuntimeError):
    """Le relais navigateur n'a pas pu transmettre la requête."""


class RelayResponse:
    """Objet compatible avec l'interface ``requests.Response`` utilisée par le provider."""

    def __init__(self, status_code: int, text: str):
        self.status_code = int(status_code)
        self.text = text or ""
        self.content = self.text.encode("utf-8", errors="replace")

    def json(self) -> Any:
        return jsonlib.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RelayError(f"HTTP {self.status_code}: {self.text[:300]}")


@dataclass
class _Pending:
    event: threading.Event
    payload: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None
    dispatched: bool = False


class RelayHub:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: Set[asyncio.Queue] = set()
        self._pending: Dict[str, _Pending] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.requests = 0
        self.responses = 0

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": True,
                "browser_connected": bool(self._subscribers),
                "pending": len(self._pending),
                "requests": self.requests,
                "responses": self.responses,
            }

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._loop = loop
            self._subscribers.add(queue)
            waiting = [
                p.payload for p in self._pending.values()
                if p.result is None and not p.dispatched
            ]
            for pending in self._pending.values():
                if pending.result is None and not pending.dispatched:
                    pending.dispatched = True
        for payload in waiting:
            queue.put_nowait(payload)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def _dispatch(self, request_id: str) -> None:
        with self._lock:
            pending = self._pending.get(request_id)
            loop = self._loop
            target = next(iter(self._subscribers), None)
            if pending is None or target is None or loop is None:
                return
            payload = pending.payload
            pending.dispatched = True
        try:
            loop.call_soon_threadsafe(target.put_nowait, payload)
        except RuntimeError:
            with self._lock:
                pending.dispatched = False

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, Any]] = None,
        body: Optional[str] = None,
        timeout: float = 60.0,
    ) -> RelayResponse:
        request_id = uuid.uuid4().hex[:12]
        payload = {
            "type": "relay_request",
            "id": request_id,
            "method": str(method).upper(),
            "url": str(url),
            "headers": {str(k): str(v) for k, v in (headers or {}).items()},
            "body": body,
        }
        pending = _Pending(threading.Event(), payload)
        with self._lock:
            self._pending[request_id] = pending
            self.requests += 1
        self._dispatch(request_id)

        waited = 0.0
        saw_browser = False
        while waited < timeout and not pending.event.is_set():
            with self._lock:
                saw_browser = bool(self._subscribers)
            started = time.monotonic()
            if pending.event.wait(min(0.5, timeout - waited)):
                break
            waited += time.monotonic() - started
            if not saw_browser and waited >= RELAY_CONNECT_GRACE:
                break

        with self._lock:
            self._pending.pop(request_id, None)
            result = pending.result
            browser_connected = bool(self._subscribers) or saw_browser

        if result is None:
            if not browser_connected:
                raise RelayError(
                    "Relais navigateur inactif : garde l'interface Web ouverte et active "
                    "« Relais navigateur » dans Réglages → Connexion. Le serveur ne peut pas "
                    "joindre cette URL directement (sortie réseau bloquée)."
                )
            raise RelayError(
                f"Le navigateur n'a pas répondu au relais dans le délai imparti ({timeout:.0f} s)."
            )
        if result.get("error"):
            raise RelayError(f"Relais navigateur : {result['error']}")
        return RelayResponse(int(result.get("status") or 502), str(result.get("body") or ""))

    def deliver(
        self,
        request_id: str,
        status: Optional[int] = None,
        body: str = "",
        error: str = "",
    ) -> bool:
        with self._lock:
            pending = self._pending.get(str(request_id))
            if pending is None or pending.result is not None:
                return False
            pending.result = {"status": status, "body": body, "error": error}
            self.responses += 1
            pending.event.set()
            return True


HUB = RelayHub()


def _timeout_value(timeout: Any, default: float) -> float:
    if isinstance(timeout, (tuple, list)):
        values = [float(v) for v in timeout if isinstance(v, (int, float))]
        timeout = max(values) if values else default
    if not isinstance(timeout, (int, float)):
        timeout = default
    return max(10.0, min(float(timeout), RELAY_MAX_WAIT))


class RelaySession:
    """Faux ``requests.Session`` : route ``get``/``post`` vers le WebSocket."""

    def __init__(self, hub: Optional[RelayHub] = None):
        self.hub = hub or HUB

    def get(self, url: str, timeout: Any = None, headers: Any = None, **kwargs: Any) -> RelayResponse:
        return self.hub.request(
            "GET", url, headers=headers, timeout=_timeout_value(timeout, 30.0)
        )

    def post(
        self,
        url: str,
        json: Any = None,
        data: Any = None,
        headers: Any = None,
        timeout: Any = None,
        **kwargs: Any,
    ) -> RelayResponse:
        sent_headers = dict(headers or {})
        if json is not None:
            body = json if isinstance(json, str) else jsonlib.dumps(json, ensure_ascii=False)
            sent_headers.setdefault("Content-Type", "application/json")
        elif data is not None:
            body = data if isinstance(data, str) else str(data)
        else:
            body = None
        return self.hub.request(
            "POST", url, headers=sent_headers, body=body,
            timeout=_timeout_value(timeout, 180.0),
        )


def relay_enabled(config: Dict[str, Any]) -> bool:
    nemapi = config.get("nemapi") if isinstance(config, dict) else None
    if not isinstance(nemapi, dict):
        return False
    return bool(nemapi.get("browser_relay")) and bool(str(nemapi.get("url") or "").strip())


def attach_to_provider(provider: Any, config: Dict[str, Any]) -> bool:
    """Remplace la session HTTP du provider par le relais, si activé."""
    if not relay_enabled(config):
        return False
    provider._session = RelaySession(HUB)
    provider._browser_relay = True
    return True
