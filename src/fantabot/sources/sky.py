"""Fonte: Sky Sport - probabili formazioni Serie A.

Sky non pubblica percentuali, ma divide i giocatori in sezioni esplicite
(Titolari, Riserve, In dubbio, Squalificati, Indisponibili, Allenatore), che
mappiamo direttamente sui nostri stati.

Struttura verificata sul markup live (settembre 2026):

    label.ftbl__teams-tabs__tab--left[data-team-name]   -> squadra di casa
    label.ftbl__teams-tabs__tab--right[data-team-name]  -> squadra ospite
    span.ftbl__formation-home / span.ftbl__formation-away -> "3-5-2"
    table.ftbl__report-tabs-panel
      tr.ftbl__report-tabs-panel__header > td           -> "Titolari" | "In dubbio" | ...
      tr.ftbl__report-tabs-panel__col[data-panel-left|right]
        a.ftbl__match-formation-player--name            -> "Bijlow J."   (Titolari)
        td.ftbl__report-tabs-panel__joined-array        -> "Rossi, Bianchi" (altre sezioni)

I nomi arrivano come "Cognome I." e vengono normalizzati da `fantabot.names`.
"""

from __future__ import annotations

import logging

from selectolax.parser import HTMLParser, Node

from fantabot.models import SourceEntry, SourceReport, Status
from fantabot.sources.base import ProbableSource

log = logging.getLogger(__name__)

#: Intestazione di sezione (normalizzata a minuscolo) -> stato.
_SECTION_STATUS = {
    "titolari": Status.STARTER,
    "in dubbio": Status.DOUBT,
    "ballottaggi": Status.DOUBT,
    "riserve": Status.BENCH,
    "panchina": Status.BENCH,
    "squalificati": Status.OUT,
    "indisponibili": Status.OUT,
    "infortunati": Status.OUT,
}

#: Sezioni da ignorare (non contengono giocatori schierabili).
_SKIP_SECTIONS = {"allenatore", "arbitro"}


class SkySource(ProbableSource):
    key = "sky"
    label = "Sky Sport"

    def _fetch(self) -> SourceReport:
        url = self.option("url", "https://sport.sky.it/calcio/serie-a/probabili-formazioni")
        result = self.ctx.client.get(url)
        self.ctx.save_raw("sky.html", result.text)
        return self.parse(result.text)

    def parse(self, html: str) -> SourceReport:
        tree = HTMLParser(html)
        report = SourceReport(source=self.label, ok=True)

        for table in tree.css("table.ftbl__report-tabs-panel"):
            teams = _teams_for_table(table)
            if teams is None:
                continue
            home, away, form_home, form_away = teams
            report.teams.update({home, away})

            section = Status.STARTER
            for row in table.css("tr"):
                classes = row.attributes.get("class") or ""

                if "__header" in classes:
                    label = row.text(strip=True).lower()
                    if label in _SKIP_SECTIONS:
                        section = None
                    else:
                        section = _SECTION_STATUS.get(label, Status.BENCH)
                    continue

                if section is None or "__col" not in classes:
                    continue

                is_home = row.attributes.get("data-panel-left") is not None
                team = home if is_home else away
                formation = form_home if is_home else form_away

                for name in _names_in_row(row):
                    report.entries.append(
                        SourceEntry(
                            source=self.label,
                            player_name=name,
                            team=team,
                            status=section,
                            team_formation=formation,
                        )
                    )

        if not report.entries:
            report.ok = False
            report.error = "nessun giocatore trovato: struttura della pagina cambiata?"
        return report


def _teams_for_table(table: Node) -> tuple[str, str, str | None, str | None] | None:
    """Risale dal `table` al contenitore della partita per leggere le squadre."""
    node: Node | None = table
    for _ in range(8):  # il markup Sky annida di poco; 8 livelli sono abbondanti
        node = node.parent if node is not None else None
        if node is None:
            return None
        left = node.css_first("label[data-tab-left]")
        right = node.css_first("label[data-tab-right]")
        if left is not None and right is not None:
            home = (left.attributes.get("data-team-name") or "").strip()
            away = (right.attributes.get("data-team-name") or "").strip()
            if not home or not away:
                return None
            return (
                home,
                away,
                _text_or_none(node.css_first(".ftbl__formation-home")),
                _text_or_none(node.css_first(".ftbl__formation-away")),
            )
    return None


def _names_in_row(row: Node) -> list[str]:
    """Nomi dei giocatori in una riga, sia formato "titolari" sia lista unita."""
    names = [
        n.text(strip=True)
        for n in row.css("a.ftbl__match-formation-player--name")
        if n.text(strip=True)
    ]
    if names:
        return names

    out: list[str] = []
    for cell in row.css("td.ftbl__report-tabs-panel__joined-array"):
        text = cell.text(strip=True)
        if not text or text == "-":
            continue
        out.extend(part.strip() for part in text.split(",") if part.strip())
    return out


def _text_or_none(node: Node | None) -> str | None:
    if node is None:
        return None
    text = node.text(strip=True)
    return text or None
