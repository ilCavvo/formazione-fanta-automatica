"""Notifiche Telegram.

Due tipi di messaggio:
- riepilogo di fine run (formazione schierata, dubbi risolti, esito submit);
- alert critico (login fallito, sito cambiato, nessuna fonte disponibile),
  mandato appena l'errore si verifica cosi' c'e' tempo di intervenire a mano.

Il messaggio e' costruito da `RunResult`: se manca il token il modulo lo dice e
prosegue senza crashare, perche' una notifica persa non deve far fallire un run
che ha gia' schierato la formazione.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime

import httpx

from fantabot.models import Lineup, Role, RunResult, Status

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"

#: Telegram taglia i messaggi oltre i 4096 caratteri.
_MAX_LEN = 4000

_ROLE_LABEL = {Role.P: "P", Role.D: "D", Role.C: "C", Role.A: "A"}

_STATUS_ICON = {
    Status.STARTER: "✅",   # titolare confermato
    Status.DOUBT: "❓",     # dubbio
    Status.BENCH: "\U0001f7e1", # dato in panchina ma schierato lo stesso
    Status.UNKNOWN: "❔",   # nessuna fonte
    Status.OUT: "⛔",       # indisponibile
}


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None, enabled: bool = True,
                 parse_mode: str = "HTML", timeout_s: float = 20.0) -> None:
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled
        self.parse_mode = parse_mode
        self.timeout_s = timeout_s

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.configured:
            log.warning("Telegram non configurato: messaggio non inviato")
            log.info("messaggio che sarebbe stato inviato:\n%s", text)
            return False

        for chunk in _split(text, _MAX_LEN):
            try:
                response = httpx.post(
                    _API.format(token=self.token),
                    json={
                        "chat_id": self.chat_id,
                        "text": chunk,
                        "parse_mode": self.parse_mode,
                        "disable_web_page_preview": True,
                    },
                    timeout=self.timeout_s,
                )
                if response.status_code != 200:
                    log.error("Telegram ha risposto %s: %s",
                              response.status_code, response.text[:300])
                    return False
            except httpx.HTTPError as exc:
                log.error("invio Telegram fallito: %s", exc)
                return False
        return True

    def send_alert(self, title: str, detail: str) -> bool:
        message = f"\U0001f6a8 <b>{html.escape(title)}</b>\n\n{html.escape(detail)}"
        return self.send(message)

    def send_result(self, result: RunResult) -> bool:
        return self.send(format_result(result))


# --------------------------------------------------------------------------
# Formattazione (funzione pura: testata senza rete)
# --------------------------------------------------------------------------


def format_result(result: RunResult, include_decisions: bool = True,
                  include_bench: bool = False) -> str:
    lines: list[str] = []

    if not result.ok:
        lines.append("\U0001f6a8 <b>Fantabot: run FALLITO</b>")
    elif result.dry_run:
        lines.append("\U0001f9ea <b>Fantabot: DRY RUN</b> (formazione non inviata)")
    elif result.submitted:
        lines.append("✅ <b>Fantabot: formazione inviata</b>")
    else:
        lines.append("⚠️ <b>Fantabot: formazione NON inviata</b>")

    if result.matchday is not None:
        deadline = result.matchday.deadline
        when = deadline.strftime("%a %d/%m %H:%M") if deadline else "sconosciuta"
        lines.append(
            f"Giornata <b>{result.matchday.matchweek}</b> — deadline {html.escape(when)}"
        )

    lines.append("")
    lines.append(_format_sources(result))

    if result.lineup is not None:
        lines.append("")
        lines.append(_format_lineup(result.lineup))

        if include_decisions and result.lineup.decisions:
            lines.append("")
            lines.append("<b>Scelte e dubbi</b>")
            for decision in result.lineup.decisions:
                lines.append(f"• {html.escape(decision)}")

        if result.lineup.warnings:
            lines.append("")
            lines.append("<b>Avvisi</b>")
            for warning in result.lineup.warnings:
                lines.append(f"⚠️ {html.escape(warning)}")

        if include_bench and result.lineup.bench:
            lines.append("")
            lines.append("<b>Panchina</b> (ordine di subentro)")
            bench = ", ".join(
                f"{_ROLE_LABEL[p.role]} {p.player.name}" for p in result.lineup.bench
            )
            lines.append(html.escape(bench))

    if result.submit_detail:
        lines.append("")
        lines.append(f"<i>{html.escape(result.submit_detail)}</i>")

    if result.error:
        lines.append("")
        lines.append(f"<b>Errore:</b> {html.escape(result.error)}")

    lines.append("")
    lines.append(f"<i>{_duration(result)}</i>")
    return "\n".join(lines)


def _format_sources(result: RunResult) -> str:
    if not result.sources:
        return "<b>Fonti</b>: nessuna interrogata"
    parts = []
    for report in result.sources:
        if report.ok:
            parts.append(f"✅ {html.escape(report.source)} ({len(report.entries)})")
        else:
            reason = html.escape((report.error or "non disponibile")[:80])
            parts.append(f"❌ {html.escape(report.source)} — {reason}")
    return "<b>Fonti</b>\n" + "\n".join(parts)


def _format_lineup(lineup: Lineup) -> str:
    lines = [f"<b>Formazione {html.escape(lineup.module)}</b>"]
    for role in (Role.P, Role.D, Role.C, Role.A):
        players = lineup.by_role(role)
        if not players:
            continue
        rendered = ", ".join(
            f"{_STATUS_ICON.get(p.verdict.status, '')}{html.escape(p.player.name)}"
            for p in players
        )
        lines.append(f"<b>{_ROLE_LABEL[role]}</b> — {rendered}")
    return "\n".join(lines)


def _duration(result: RunResult) -> str:
    if result.started_at is None:
        return ""
    end = result.finished_at or datetime.now(result.started_at.tzinfo)
    seconds = (end - result.started_at).total_seconds()
    return f"run completato in {seconds:.0f}s"


def _split(text: str, limit: int) -> list[str]:
    """Spezza sui newline per non tagliare un tag HTML a meta'."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.split("\n"):
        if length + len(line) + 1 > limit and current:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks
