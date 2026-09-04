"""Parser delle fonti, verificati su HTML reale ritagliato dalle pagine live.

Le fixture in `tests/fixtures/` sono porzioni vere delle pagine pubbliche
(settembre 2026). Se una fonte cambia markup, questi test falliscono e dicono
esattamente cosa aggiustare.
"""

from __future__ import annotations

from collections import Counter

import pytest

from fantabot.models import Role, Status
from fantabot.sources.fantacalcio_it import FantacalcioItSource
from fantabot.sources.gazzetta import GazzettaSource
from fantabot.sources.sky import SkySource
from fantabot.sources.unavailability import UnavailabilityFeed


def parse(cls, html: str):
    """Istanzia il parser senza toccare la rete."""
    return cls.__new__(cls).parse(html)


class TestFantacalcioIt:
    @pytest.fixture
    def report(self, fixture_html):
        return parse(FantacalcioItSource, fixture_html("fantacalcio_it.html"))

    def test_legge_squadre_e_giornata(self, report):
        assert report.ok
        assert report.matchweek == 3
        assert report.teams == {"Genoa", "Como"}

    def test_ruoli_e_percentuali(self, report):
        bijlow = next(e for e in report.entries if e.player_name == "Bijlow")
        assert bijlow.role is Role.P
        assert bijlow.probability == 90.0
        assert bijlow.status is Status.STARTER
        assert bijlow.team_formation == "3-5-2"

    def test_percentuale_bassa_diventa_dubbio(self, report):
        dubbi = [e for e in report.entries if e.status is Status.DOUBT]
        assert dubbi, "la fixture contiene ballottaggi al 60%"
        assert all(40.0 <= e.probability < 70.0 for e in dubbi if e.probability)

    def test_riserve_marcate_panchina(self, report):
        conteggio = Counter(e.status for e in report.entries)
        assert conteggio[Status.BENCH] > 0
        assert conteggio[Status.STARTER] > 0

    def test_html_vuoto_non_esplode(self):
        report = parse(FantacalcioItSource, "<html><body></body></html>")
        assert report.ok is False
        assert "struttura" in report.error


class TestSky:
    @pytest.fixture
    def report(self, fixture_html):
        return parse(SkySource, fixture_html("sky.html"))

    def test_legge_le_due_squadre(self, report):
        assert report.ok
        assert report.teams == {"Genoa", "Como"}

    def test_undici_titolari_per_squadra(self, report):
        titolari = Counter(
            e.team for e in report.entries if e.status is Status.STARTER
        )
        assert titolari["Genoa"] == 11
        assert titolari["Como"] == 11

    def test_nomi_con_iniziale_puntata(self, report):
        nomi = {e.player_name for e in report.entries}
        assert any(n.endswith(".") for n in nomi), "Sky scrive 'Cognome I.'"

    def test_sezioni_indisponibili(self, report):
        out = [e for e in report.entries if e.status is Status.OUT]
        assert out, "la fixture contiene la sezione Indisponibili"

    def test_modulo_per_squadra(self, report):
        genoa = next(e for e in report.entries if e.team == "Genoa")
        assert genoa.team_formation == "3-5-2"

    def test_allenatore_non_diventa_giocatore(self, report):
        # La riga "Allenatore" e' esplicitamente saltata dal parser.
        assert not any("allenatore" in e.player_name.lower() for e in report.entries)

    def test_html_vuoto_non_esplode(self):
        report = parse(SkySource, "<html><body></body></html>")
        assert report.ok is False


class TestGazzetta:
    def test_riconosce_il_formato_agenzia(self):
        html = (
            "<p>GENOA (3-5-2): Bijlow; Marcandalli, Ostigard, Vasquez; Sabelli, "
            "Frendrup, Masini, Malinovskyi, Martin; Colombo, Osmajic.</p>"
        )
        report = parse(GazzettaSource, html)
        assert report.ok
        assert report.teams == {"Genoa"}
        titolari = [e for e in report.entries if e.status is Status.STARTER]
        assert len(titolari) == 11
        assert titolari[0].player_name == "Bijlow"
        assert titolari[0].team_formation == "3-5-2"

    def test_prosa_senza_formazioni_fallisce_in_modo_pulito(self):
        html = "<p>La 3a giornata si apre con Genoa-Como venerdi alle 20.45.</p>"
        report = parse(GazzettaSource, html)
        assert report.ok is False
        assert "nessuna formazione" in report.error

    def test_riga_con_meno_di_undici_nomi_viene_ignorata(self):
        html = "<p>GENOA (3-5-2): Bijlow; Marcandalli, Ostigard.</p>"
        report = parse(GazzettaSource, html)
        assert report.ok is False


class TestIndisponibili:
    @pytest.fixture
    def unavailable(self, fixture_html):
        return UnavailabilityFeed.parse(fixture_html("indisponibili.html"))

    def test_legge_infortunati_con_squadra_e_motivo(self, unavailable):
        assert unavailable
        primo = unavailable[0]
        assert primo.team
        assert primo.kind == "infortunio"
        assert primo.reason

    def test_i_diffidati_non_sono_indisponibili(self, unavailable):
        """Un diffidato gioca: non deve mai comparire in questa lista."""
        assert all(item.kind in {"infortunio", "squalifica"} for item in unavailable)

    def test_html_vuoto(self):
        assert UnavailabilityFeed.parse("<html></html>") == []
