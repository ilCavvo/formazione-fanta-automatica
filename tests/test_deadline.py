"""Calendario e deadline di schieramento."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from fantabot.deadline import CalendarError, SerieACalendar, effective_deadline

ROME = ZoneInfo("Europe/Rome")


@pytest.fixture
def calendar() -> SerieACalendar:
    cal = SerieACalendar.__new__(SerieACalendar)  # niente rete nei test
    cal.tz = ROME
    return cal


class TestParsing:
    def test_legge_le_partite(self, calendar, fixture_html):
        matches = calendar.parse_matches(fixture_html("calendario.html"))
        assert len(matches) == 5
        assert matches[0].home == "Genoa"
        assert matches[0].away == "Como"
        assert all(m.matchweek == 3 for m in matches)

    def test_orario_di_inizio(self, calendar, fixture_html):
        matches = calendar.parse_matches(fixture_html("calendario.html"))
        assert matches[0].kickoff == datetime(2026, 9, 4, 20, 45, tzinfo=ROME)

    def test_deadline_e_il_primo_anticipo(self, calendar, fixture_html):
        matchday = calendar.parse_matchday(
            fixture_html("calendario.html"),
            now=datetime(2026, 9, 4, 10, 0, tzinfo=ROME),
        )
        assert matchday.matchweek == 3
        assert matchday.deadline == datetime(2026, 9, 4, 20, 45, tzinfo=ROME)
        assert matchday.deadline_source == "calendario"

    def test_squadre_della_giornata(self, calendar, fixture_html):
        matchday = calendar.parse_matchday(
            fixture_html("calendario.html"),
            now=datetime(2026, 9, 4, 10, 0, tzinfo=ROME),
        )
        assert "Genoa" in matchday.teams
        assert "Torino" in matchday.teams

    def test_calendario_vuoto(self, calendar):
        with pytest.raises(CalendarError):
            calendar.parse_matchday("<html><body></body></html>")

    def test_placeholder_1970_ignorato(self, calendar):
        """La pagina delle probabili usa 1970-01-01: non e' una data vera."""
        html = """
        <li class="match">
          <div class="matchweek">3</div>
          <label class="team-home"><meta itemprop="name" content="Genoa"/></label>
          <label class="team-away"><meta itemprop="name" content="Como"/></label>
          <div class="match-date">
            <meta itemprop="startDate" content="1970-01-01"/>
            <span class="hours">01:00</span>
          </div>
        </li>
        """
        matches = calendar.parse_matches(html)
        assert len(matches) == 1
        assert matches[0].kickoff is None


class TestSceltaGiornata:
    def _html(self, weeks: dict[int, str]) -> str:
        blocks = []
        for week, date in weeks.items():
            blocks.append(
                f'<li class="match"><div class="matchweek">{week}</div>'
                f'<label class="team-home"><meta itemprop="name" content="A{week}"/></label>'
                f'<label class="team-away"><meta itemprop="name" content="B{week}"/></label>'
                f'<div class="match-date"><meta itemprop="startDate" content="{date}"/>'
                f'<span class="hours">15:00</span></div></li>'
            )
        return "<ul>" + "".join(blocks) + "</ul>"

    def test_prende_la_prima_giornata_non_ancora_conclusa(self, calendar):
        html = self._html({3: "2026-09-06", 4: "2026-09-13"})
        matchday = calendar.parse_matchday(
            html, now=datetime(2026, 9, 5, 12, 0, tzinfo=ROME)
        )
        assert matchday.matchweek == 3

    def test_passa_alla_successiva_quando_la_precedente_e_finita(self, calendar):
        html = self._html({3: "2026-09-06", 4: "2026-09-13"})
        matchday = calendar.parse_matchday(
            html, now=datetime(2026, 9, 8, 12, 0, tzinfo=ROME)
        )
        assert matchday.matchweek == 4

    def test_tutte_passate_prende_lultima(self, calendar):
        html = self._html({37: "2026-05-17", 38: "2026-05-24"})
        matchday = calendar.parse_matchday(
            html, now=datetime(2026, 6, 1, 12, 0, tzinfo=ROME)
        )
        assert matchday.matchweek == 38


class TestMargineDiSicurezza:
    def test_sottrae_i_minuti_di_margine(self, calendar, fixture_html):
        matchday = calendar.parse_matchday(
            fixture_html("calendario.html"),
            now=datetime(2026, 9, 4, 10, 0, tzinfo=ROME),
        )
        assert effective_deadline(matchday, 20) == datetime(2026, 9, 4, 20, 25, tzinfo=ROME)

    def test_deadline_sconosciuta(self, calendar):
        from fantabot.models import Matchday

        assert effective_deadline(Matchday(3, [], None), 20) is None
