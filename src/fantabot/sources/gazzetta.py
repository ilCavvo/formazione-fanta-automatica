"""Fonte: Gazzetta dello Sport - probabili formazioni (best effort).

Attenzione, differenza importante rispetto alle altre due fonti: Gazzetta non
pubblica una pagina strutturata di probabili formazioni. Il gioco ufficiale
(magic.gazzetta.it) e' una web app Flutter non scrapabile, e l'articolo-guida
di giornata su gazzetta.it e' prosa parzialmente a pagamento.

Questo adattatore fa quindi il massimo che si puo' fare in modo "gentile":

1. cerca nell'indice `calcio/fantanews/` l'articolo-guida della giornata,
2. ne estrae il testo,
3. cerca il formato tipico delle agenzie: `SQUADRA (3-5-2): Tizio; Caio, Sempronio`.

Se non trova nulla ritorna `ok=False` con un errore parlante: l'aggregazione
prosegue sulle fonti rimaste, come previsto dal fallback in `aggregate.py`.
Quando la fonte e' assente il messaggio Telegram lo dice esplicitamente.
"""

from __future__ import annotations

import html as html_lib
import logging
import re

from selectolax.parser import HTMLParser

from fantabot.models import SourceEntry, SourceReport, Status
from fantabot.sources.base import ProbableSource

log = logging.getLogger(__name__)

#: Link agli articoli di probabili formazioni nell'indice fantanews.
_ARTICLE_HREF = re.compile(r"probabili-formazioni", re.I)

#: "GENOA (3-5-2): Bijlow; Marcandalli, Ostigard, Vasquez; ... Colombo."
_LINEUP_LINE = re.compile(
    r"^\s*(?P<team>[A-ZÀ-Ü][A-Za-zÀ-ü'\s\.]{2,30}?)\s*\((?P<module>\d(?:-\d){1,3})\)\s*:\s*"
    r"(?P<players>.+?)\s*$"
)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


class GazzettaSource(ProbableSource):
    key = "gazzetta"
    label = "Gazzetta"

    def _fetch(self) -> SourceReport:
        index_url = self.option("index_url", "https://www.gazzetta.it/calcio/fantanews/")
        index = self.ctx.client.get(index_url)
        self.ctx.save_raw("gazzetta_index.html", index.text)

        article_url = self._find_article(index.text, index_url)
        if article_url is None:
            raise RuntimeError("nessun articolo di probabili formazioni trovato nell'indice")

        article = self.ctx.client.get(article_url)
        self.ctx.save_raw("gazzetta_article.html", article.text)

        report = self.parse(article.text)
        if not report.ok:
            report.error = (
                f"articolo trovato ({article_url}) ma senza formazioni leggibili "
                "(prosa o contenuto a pagamento)"
            )
        return report

    def _find_article(self, index_html: str, base_url: str) -> str | None:
        tree = HTMLParser(index_html)
        for link in tree.css("a[href]"):
            href = link.attributes.get("href") or ""
            if not _ARTICLE_HREF.search(href):
                continue
            if "mondiali" in href.lower():  # l'indice linka anche i Mondiali
                continue
            if href.startswith("http"):
                return href
            if href.startswith("/"):
                root = "/".join(base_url.split("/")[:3])
                return root + href
        return None

    def parse(self, html: str) -> SourceReport:
        report = SourceReport(source=self.label, ok=True)
        for line in _text_lines(html):
            match = _LINEUP_LINE.match(line)
            if not match:
                continue
            team = _WS.sub(" ", match.group("team")).strip().title()
            module = match.group("module")
            players = _split_players(match.group("players"))
            if len(players) < 11:
                # Meno di 11 nomi significa quasi sempre che abbiamo agganciato
                # una frase di prosa, non una formazione.
                continue
            report.teams.add(team)
            for name in players[:11]:
                report.entries.append(
                    SourceEntry(
                        source=self.label,
                        player_name=name,
                        team=team,
                        status=Status.STARTER,
                        team_formation=module,
                    )
                )
            for name in players[11:]:
                report.entries.append(
                    SourceEntry(source=self.label, player_name=name, team=team,
                                status=Status.BENCH, team_formation=module)
                )

        if not report.entries:
            report.ok = False
            report.error = "nessuna formazione riconosciuta nel testo dell'articolo"
        return report


def _text_lines(html: str) -> list[str]:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    body = body.replace("</p>", "\n").replace("<br", "\n<br")
    body = _TAG.sub("\n", body)
    body = html_lib.unescape(body)
    return [line.strip() for line in body.split("\n") if line.strip()]


def _split_players(blob: str) -> list[str]:
    """Le formazioni separano i reparti con `;` e i giocatori con `,`."""
    parts: list[str] = []
    for chunk in re.split(r"[;,]", blob):
        name = chunk.strip().rstrip(".").strip()
        # Scarta code di prosa e note tra parentesi.
        name = re.sub(r"\s*\([^)]*\)", "", name).strip()
        if not name or len(name) > 30 or " " in name and len(name.split()) > 3:
            continue
        parts.append(name)
    return parts
