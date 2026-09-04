"""Infortunati e squalificati (fantacalcio.it/indisponibili-serie-a).

Chi compare qui non puo' essere schierato, a prescindere da cosa dicono le
probabili: viene marcato `Status.OUT` e sostituito dal primo disponibile per
ruolo secondo l'ordine di rosa.

Struttura verificata sul markup live (settembre 2026):

    div.card.team-card
      header.team-info span.team-name    -> "Atalanta"
      div.col
        header ... "Infortunati" | "Squalificati" | "Diffidati"
        ul.unstyled li strong.item-name  -> "Sulemana K."
                       div.item-description -> motivo
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from selectolax.parser import HTMLParser, Node

from fantabot.http import PoliteClient

log = logging.getLogger(__name__)

#: Etichette che rendono un giocatore non schierabile. I "diffidati" NO:
#: un diffidato gioca, rischia solo la squalifica alla prossima ammonizione.
_BLOCKING_LABELS = ("infortunati", "squalificati", "indisponibili")


@dataclass(frozen=True)
class Unavailable:
    name: str
    team: str
    reason: str
    #: "infortunio" oppure "squalifica"
    kind: str


class UnavailabilityFeed:
    """Elenco degli indisponibili di giornata."""

    def __init__(self, client: PoliteClient, url: str) -> None:
        self.client = client
        self.url = url

    def fetch(self) -> list[Unavailable]:
        result = self.client.get(self.url)
        return self.parse(result.text)

    @staticmethod
    def parse(html: str) -> list[Unavailable]:
        tree = HTMLParser(html)
        out: list[Unavailable] = []

        for card in tree.css("div.team-card"):
            team_node = card.css_first(".team-name")
            team = team_node.text(strip=True) if team_node is not None else ""
            if not team:
                continue

            for column in card.css("div.col"):
                label = _column_label(column)
                if not any(word in label for word in _BLOCKING_LABELS):
                    continue
                kind = "squalifica" if "squalificati" in label else "infortunio"
                for item in column.css("li"):
                    name_node = item.css_first(".item-name")
                    if name_node is None:
                        continue
                    name = name_node.text(strip=True)
                    if not name:
                        continue
                    desc_node = item.css_first(".item-description")
                    reason = desc_node.text(strip=True) if desc_node is not None else ""
                    out.append(
                        Unavailable(name=name, team=team, reason=reason[:180], kind=kind)
                    )

        log.info("indisponibili: %d giocatori", len(out))
        return out


def _column_label(column: Node) -> str:
    """Etichetta della colonna (Infortunati/Squalificati/Diffidati), minuscola.

    Una colonna puo' contenere piu' sezioni: teniamo solo la prima intestazione,
    perche' le `li` successive appartengono a quella.
    """
    header = column.css_first("header")
    return header.text(strip=True).lower() if header is not None else ""
