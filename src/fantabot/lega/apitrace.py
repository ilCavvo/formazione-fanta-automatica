"""Registrazione delle chiamate di rete dell'app della lega.

L'app di `leghe.fantacalcio.it` e' Angular: rosa e formazione viaggiano su
chiamate JSON verso `apileague.fantacalcio.it`. Questo modulo le registra
mentre il browser lavora, per poter poi riscrivere lettura e invio come
semplici richieste HTTP invece che come automazione del DOM.

Regola fondamentale: **il registro e' pensato per essere pubblicato** (finisce
nel log del job e negli artifact). Quindi:

- dei corpi si registra la *forma* — chiavi e tipi — non i valori, tranne gli
  scalari brevi e non sensibili, che servono a capire il formato;
- le chiavi sensibili sono sempre sostituite;
- le credenziali note vengono cancellate ovunque compaiano;
- delle query string si tengono i nomi dei parametri, non i valori.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

#: Chiavi il cui valore non deve mai comparire nel registro.
SENSITIVE_KEYS = frozenset({
    "password", "passwd", "pwd", "pass",
    "token", "access_token", "refresh_token", "id_token", "jwt",
    "authorization", "auth", "secret", "apikey", "api_key",
    "email", "mail", "username", "user", "login",
})

#: Oltre questa lunghezza un valore scalare viene troncato.
MAX_VALUE = 40

#: Profondita' massima esplorata in un corpo JSON.
MAX_DEPTH = 4

#: Elementi di lista descritti prima di riassumere il resto.
MAX_ITEMS = 2

_REDACTED = "***"


@dataclass
class ApiCall:
    method: str
    host: str
    path: str
    #: Solo i nomi dei parametri: i valori possono essere identificativi di sessione.
    query_keys: list[str] = field(default_factory=list)
    status: int | None = None
    request_shape: Any | None = None
    response_shape: Any | None = None

    @property
    def key(self) -> str:
        return f"{self.method} {self.host}{self.path}"


class ApiTrace:
    """Raccoglie le chiamate JSON verso gli host indicati."""

    def __init__(self, hosts: tuple[str, ...] = ("fantacalcio.it",),
                 secrets: tuple[str, ...] = ()) -> None:
        self.hosts = hosts
        # Le credenziali vere: cancellate ovunque compaiano, anche dentro
        # una chiave che non avremmo classificato come sensibile.
        self._secrets = tuple(s for s in secrets if s and len(s) >= 4)
        self.calls: list[ApiCall] = []
        self._by_key: dict[str, ApiCall] = {}

    # -- raccolta -----------------------------------------------------------

    def wants(self, url: str) -> bool:
        host = urlparse(url).netloc
        return any(host == h or host.endswith("." + h) for h in self.hosts)

    def record_request(self, method: str, url: str, body: str | None,
                       resource_type: str = "") -> ApiCall | None:
        """Registra una chiamata. Ritorna `None` se non ci interessa."""
        if not self.wants(url):
            return None
        # Solo chiamate applicative: le pagine, le immagini e i fogli di stile
        # non dicono nulla su come si salva una formazione.
        if resource_type and resource_type not in {"xhr", "fetch"}:
            return None

        parsed = urlparse(url)
        call = ApiCall(
            method=method.upper(),
            host=parsed.netloc,
            path=parsed.path,
            query_keys=sorted({p.split("=", 1)[0] for p in parsed.query.split("&") if p}),
            request_shape=self.shape(_parse_json(body)) if body else None,
        )

        existing = self._by_key.get(call.key)
        if existing is not None:
            # Stessa chiamata gia' vista: teniamo la prima, che basta a
            # descrivere il formato, senza gonfiare il registro.
            if existing.request_shape is None:
                existing.request_shape = call.request_shape
            return existing

        self.calls.append(call)
        self._by_key[call.key] = call
        return call

    def record_response(self, url: str, method: str, status: int,
                        body: str | None = None) -> None:
        parsed = urlparse(url)
        call = self._by_key.get(f"{method.upper()} {parsed.netloc}{parsed.path}")
        if call is None:
            return
        call.status = status
        if body and call.response_shape is None:
            call.response_shape = self.shape(_parse_json(body))

    # -- riduzione a forma pubblicabile -------------------------------------

    def shape(self, value: Any, depth: int = 0) -> Any:
        """Descrive un valore senza esporne il contenuto sensibile."""
        if depth >= MAX_DEPTH:
            return "..."
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in value.items():
                if str(key).lower() in SENSITIVE_KEYS:
                    out[str(key)] = _REDACTED
                else:
                    out[str(key)] = self.shape(item, depth + 1)
            return out
        if isinstance(value, list):
            if not value:
                return []
            described = [self.shape(v, depth + 1) for v in value[:MAX_ITEMS]]
            if len(value) > MAX_ITEMS:
                described.append(f"... ({len(value)} elementi in tutto)")
            return described
        return self._scalar(value)

    def _scalar(self, value: Any) -> Any:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return value
        text = str(value)
        for secret in self._secrets:
            if secret in text:
                return _REDACTED
        return text[:MAX_VALUE] + ("..." if len(text) > MAX_VALUE else "")

    # -- report --------------------------------------------------------------

    def to_markdown(self) -> str:
        lines = [
            "# fantabot — chiamate di rete della lega",
            "",
            "Registro delle chiamate JSON osservate mentre il browser lavora.",
            "Dei corpi e' riportata la **forma** (chiavi e tipi), non i valori",
            "sensibili: password, token e credenziali sono sostituiti, e delle",
            "query string restano solo i nomi dei parametri.",
            "",
        ]
        if not self.calls:
            lines.append("Nessuna chiamata registrata.")
            return "\n".join(lines)

        for call in self.calls:
            stato = f" -> {call.status}" if call.status is not None else ""
            lines.append(f"## {call.method} `{call.host}{call.path}`{stato}")
            lines.append("")
            if call.query_keys:
                lines.append(f"- parametri: {', '.join(call.query_keys)}")
            if call.request_shape is not None:
                lines.append("- corpo della richiesta:")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(call.request_shape, indent=2, ensure_ascii=False))
                lines.append("```")
            if call.response_shape is not None:
                lines.append("- forma della risposta:")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(call.response_shape, indent=2, ensure_ascii=False))
                lines.append("```")
            lines.append("")
        return "\n".join(lines)


def _parse_json(body: str | None) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return f"<non JSON, {len(body)} caratteri>"
