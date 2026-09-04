"""Fonte: fantacalcio.it - probabili formazioni Serie A.

E' la fonte piu' ricca: per ogni giocatore espone ruolo, percentuale di
titolarita' (es. 90%, 60%) e uno stato (`data-status`), oltre al modulo previsto
per la squadra e alle sezioni ballottaggi/squalificati/diffidati.

Struttura verificata sul markup live (settembre 2026):

    div.card.team-card
      header h3.team-name            -> "Genoa"
      header div.team-formation      -> "3-5-2"
      ul.player-list.starters li.player-item[data-status]
        span.role[data-value=p|d|c|a]
        a.player-name span           -> "Bijlow"
        div.progress-value           -> "90%"
      ul.player-list.reserves li.player-item ...
"""

from __future__ import annotations

import logging
import re

from selectolax.parser import HTMLParser, Node

from fantabot.models import Role, SourceEntry, SourceReport, Status
from fantabot.sources.base import ProbableSource

log = logging.getLogger(__name__)

_PERCENT = re.compile(r"(\d+)\s*%")

#: `data-status` del sito -> nostro stato, quando la percentuale non basta.
_STATUS_MAP = {
    "success": Status.STARTER,
    "warn": Status.DOUBT,
    "danger": Status.DOUBT,
}

#: Soglie sulla percentuale di titolarita' dichiarata dal sito.
_PROB_STARTER = 70.0
_PROB_DOUBT = 40.0


class FantacalcioItSource(ProbableSource):
    key = "fantacalcio_it"
    label = "Fantacalcio.it"

    def _fetch(self) -> SourceReport:
        url = self.option("url", "https://www.fantacalcio.it/probabili-formazioni-serie-a")
        result = self.ctx.client.get(url)
        self.ctx.save_raw("fantacalcio_it.html", result.text)
        return self.parse(result.text)

    # Separato da `_fetch` cosi' i test lo esercitano su HTML salvato.
    def parse(self, html: str) -> SourceReport:
        tree = HTMLParser(html)
        report = SourceReport(source=self.label, ok=True)
        report.matchweek = _parse_matchweek(tree)

        for card in tree.css("div.team-card"):
            team = _text(card.css_first("h3.team-name"))
            if not team:
                continue
            formation = _text(card.css_first(".team-formation")) or None
            report.teams.add(team)

            for list_node in card.css("ul.player-list"):
                classes = (list_node.attributes.get("class") or "")
                is_starter_list = "starters" in classes
                for item in list_node.css("li.player-item"):
                    entry = _parse_item(item, team, formation, is_starter_list, self.label)
                    if entry is not None:
                        report.entries.append(entry)

        if not report.entries:
            report.ok = False
            report.error = "nessun giocatore trovato: struttura della pagina cambiata?"
        return report


def _parse_item(
    item: Node, team: str, formation: str | None, is_starter_list: bool, source: str
) -> SourceEntry | None:
    name = _text(item.css_first("a.player-name"))
    if not name:
        return None

    role_node = item.css_first("span.role")
    role: Role | None = None
    if role_node is not None:
        raw_role = role_node.attributes.get("data-value") or ""
        try:
            role = Role.parse(raw_role)
        except ValueError:
            role = None

    probability = _parse_probability(item)
    data_status = (item.attributes.get("data-status") or "").strip().lower()

    if is_starter_list:
        # La percentuale, quando c'e', e' il segnale piu' informativo:
        # `data-status` distingue solo success/warn/danger.
        if probability is not None:
            if probability >= _PROB_STARTER:
                status = Status.STARTER
            elif probability >= _PROB_DOUBT:
                status = Status.DOUBT
            else:
                status = Status.BENCH
        else:
            status = _STATUS_MAP.get(data_status, Status.STARTER)
    else:
        status = Status.BENCH

    return SourceEntry(
        source=source,
        player_name=name,
        team=team,
        status=status,
        role=role,
        probability=probability,
        team_formation=formation,
    )


def _parse_probability(item: Node) -> float | None:
    node = item.css_first(".progress-value")
    if node is not None:
        match = _PERCENT.search(node.text())
        if match:
            return float(match.group(1))
    bar = item.css_first(".progress-bar")
    if bar is not None:
        value = bar.attributes.get("aria-valuenow")
        if value and value.isdigit():
            return float(value)
    return None


def _parse_matchweek(tree: HTMLParser) -> int | None:
    for node in tree.css(".matchweek"):
        text = node.text(strip=True)
        if text.isdigit():
            return int(text)
    return None


def _text(node: Node | None) -> str:
    return node.text(strip=True) if node is not None else ""
