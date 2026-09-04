"""Calendario di Serie A e deadline di schieramento.

La deadline di una giornata coincide con il calcio d'inizio del primo anticipo.
La leggiamo dal calendario pubblico di fantacalcio.it, che espone data
(`meta[itemprop=startDate]`), orario (`span.hours`) e numero di giornata
(`div.matchweek`) per ogni partita.

Il runner puo' poi sovrascriverla con la deadline letta nella pagina formazione
della lega dopo il login (`deadline.prefer_league_deadline`), perche' alcune
leghe la anticipano.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser, Node

from fantabot.http import PoliteClient
from fantabot.models import Match, Matchday

log = logging.getLogger(__name__)

_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


class CalendarError(RuntimeError):
    pass


class SerieACalendar:
    def __init__(self, client: PoliteClient, url: str, tz: str = "Europe/Rome") -> None:
        self.client = client
        self.url = url
        self.tz = ZoneInfo(tz)

    def fetch_matchday(self, now: datetime | None = None) -> Matchday:
        result = self.client.get(self.url)
        return self.parse_matchday(result.text, now=now)

    def parse_matchday(self, html: str, now: datetime | None = None) -> Matchday:
        matches = self.parse_matches(html)
        if not matches:
            raise CalendarError("calendario vuoto: struttura della pagina cambiata?")

        now = now or datetime.now(self.tz)
        if now.tzinfo is None:
            now = now.replace(tzinfo=self.tz)

        matchday = _pick_matchday(matches, now)
        kickoffs = [m.kickoff for m in matchday if m.kickoff is not None]
        deadline = min(kickoffs) if kickoffs else None

        return Matchday(
            matchweek=matchday[0].matchweek,
            matches=matchday,
            deadline=deadline,
            deadline_source="calendario",
        )

    def parse_matches(self, html: str) -> list[Match]:
        tree = HTMLParser(html)
        out: list[Match] = []
        for node in tree.css("li.match"):
            match = self._parse_match(node)
            if match is not None:
                out.append(match)
        return out

    def _parse_match(self, node: Node) -> Match | None:
        week_node = node.css_first(".matchweek")
        if week_node is None:
            return None
        week_text = week_node.text(strip=True)
        if not week_text.isdigit():
            return None

        home = _team_name(node, "team-home")
        away = _team_name(node, "team-away")
        if not home or not away:
            return None

        return Match(
            matchweek=int(week_text),
            home=home,
            away=away,
            kickoff=self._parse_kickoff(node),
        )

    def _parse_kickoff(self, node: Node) -> datetime | None:
        date_node = node.css_first('meta[itemprop="startDate"]')
        if date_node is None:
            return None
        date_match = _DATE.match((date_node.attributes.get("content") or "").strip())
        if not date_match:
            return None
        # La pagina delle probabili usa 1970-01-01 come placeholder: scartalo.
        year = int(date_match.group(1))
        if year < 2000:
            return None

        hour, minute = 0, 0
        hours_node = node.css_first(".hours")
        if hours_node is not None:
            time_match = _TIME.match(hours_node.text(strip=True))
            if time_match:
                hour, minute = int(time_match.group(1)), int(time_match.group(2))

        return datetime(
            year, int(date_match.group(2)), int(date_match.group(3)), hour, minute,
            tzinfo=self.tz,
        )


def _team_name(node: Node, label_class: str) -> str:
    label = node.css_first(f"label.{label_class}")
    if label is None:
        return ""
    meta = label.css_first('meta[itemprop="name"]')
    if meta is not None:
        return (meta.attributes.get("content") or "").strip()
    return label.text(strip=True)


def _pick_matchday(matches: list[Match], now: datetime) -> list[Match]:
    """La giornata "corrente": la prima che non e' ancora del tutto iniziata.

    Se sono tutte passate (fine campionato o pagina vecchia) prendiamo l'ultima.
    """
    by_week: dict[int, list[Match]] = {}
    for match in matches:
        by_week.setdefault(match.matchweek, []).append(match)

    for week in sorted(by_week):
        kickoffs = [m.kickoff for m in by_week[week] if m.kickoff is not None]
        if not kickoffs:
            continue
        if max(kickoffs) >= now:
            return by_week[week]

    return by_week[max(by_week)]


def effective_deadline(matchday: Matchday, safety_margin_minutes: int) -> datetime | None:
    """Deadline con il margine di sicurezza gia' sottratto."""
    if matchday.deadline is None:
        return None
    return matchday.deadline - timedelta(minutes=safety_margin_minutes)
