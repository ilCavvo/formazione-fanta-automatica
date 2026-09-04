"""Aggregazione a maggioranza dei verdetti delle fonti.

Casi che il brief chiede esplicitamente:
- maggioranza 2 su 3
- fonte mancante -> si vota sulle rimanenti
- nessun consenso (1-1-1) -> dubbio
- infortunati/squalificati -> esclusi
"""

from __future__ import annotations

import pytest

from fantabot.aggregate import AggregationSettings, Aggregator, resolve_doubt
from fantabot.models import Role, RosterPlayer, SourceEntry, SourceReport, Status
from fantabot.sources.unavailability import Unavailable

SETTINGS = AggregationSettings(starter_threshold=0.60, doubt_threshold=0.34)


def player(name="Bijlow", team="Genoa", role=Role.P, **kw) -> RosterPlayer:
    return RosterPlayer(name=name, team=team, role=role, **kw)


def report(source: str, *entries: SourceEntry, teams=("Genoa",), ok=True) -> SourceReport:
    return SourceReport(source=source, ok=ok, entries=list(entries), teams=set(teams))


def entry(source, name="Bijlow", team="Genoa", status=Status.STARTER, **kw) -> SourceEntry:
    return SourceEntry(source=source, player_name=name, team=team, status=status, **kw)


def verdict_for(roster, reports, **kw):
    verdicts = Aggregator(SETTINGS).aggregate(roster=roster, reports=reports, **kw)
    return verdicts[0]


class TestMaggioranza:
    def test_tutte_titolare(self):
        rosa = [player()]
        v = verdict_for(rosa, [
            report("A", entry("A")), report("B", entry("B")), report("C", entry("C")),
        ])
        assert v.status is Status.STARTER
        assert v.consensus == 1.0
        assert v.vote == pytest.approx(1.0)

    def test_due_su_tre_titolare(self):
        rosa = [player()]
        v = verdict_for(rosa, [
            report("A", entry("A", status=Status.STARTER)),
            report("B", entry("B", status=Status.STARTER)),
            report("C", entry("C", status=Status.BENCH)),
        ])
        assert v.status is Status.STARTER
        assert v.vote == pytest.approx(2 / 3)

    def test_due_su_tre_panchina(self):
        rosa = [player()]
        v = verdict_for(rosa, [
            report("A", entry("A", status=Status.BENCH)),
            report("B", entry("B", status=Status.BENCH)),
            report("C", entry("C", status=Status.STARTER)),
        ])
        assert v.status is Status.BENCH

    def test_nessun_consenso_diventa_dubbio(self):
        """1 titolare, 1 dubbio, 1 panchina: media 0.5 -> dubbio."""
        rosa = [player()]
        v = verdict_for(rosa, [
            report("A", entry("A", status=Status.STARTER)),
            report("B", entry("B", status=Status.DOUBT)),
            report("C", entry("C", status=Status.BENCH)),
        ])
        assert v.status is Status.DOUBT
        assert v.vote == pytest.approx(0.5)
        assert "nessun consenso" in v.note


class TestFonteMancante:
    def test_fonte_ko_non_vota(self):
        """Con una fonte rotta la media si calcola solo sulle altre due."""
        rosa = [player()]
        rotta = SourceReport(source="C", ok=False, error="timeout")
        v = verdict_for(rosa, [
            report("A", entry("A", status=Status.STARTER)),
            report("B", entry("B", status=Status.STARTER)),
            rotta,
        ])
        assert v.status is Status.STARTER
        assert v.vote == pytest.approx(1.0)
        assert set(v.per_source) == {"A", "B"}

    def test_una_sola_fonte_disponibile(self):
        rosa = [player()]
        v = verdict_for(rosa, [report("A", entry("A", status=Status.STARTER))])
        assert v.status is Status.STARTER

    def test_nessuna_fonte_nomina_il_giocatore(self):
        rosa = [player(name="Sconosciuto")]
        v = verdict_for(rosa, [report("A", entry("A"), teams=("Milan",))])
        assert v.status is Status.UNKNOWN
        assert "nessuna fonte" in v.note

    def test_fonte_copre_la_squadra_ma_non_il_giocatore(self):
        """Se copro il Genoa e non lo nomino, sto dicendo panchina."""
        rosa = [player(name="Riserva")]
        v = verdict_for(rosa, [
            report("A", entry("A", name="Bijlow"), teams=("Genoa",)),
            report("B", entry("B", name="Riserva", status=Status.STARTER), teams=("Genoa",)),
        ])
        assert v.per_source["A"] is Status.BENCH
        assert v.vote == pytest.approx(0.5)
        assert v.status is Status.DOUBT


class TestIndisponibili:
    def test_infortunato_escluso(self):
        rosa = [player()]
        v = verdict_for(
            rosa,
            [report("A", entry("A", status=Status.STARTER))],
            unavailable=[Unavailable(name="Bijlow", team="Genoa",
                                     reason="lesione", kind="infortunio")],
        )
        assert v.status is Status.OUT
        assert v.out_reason == "infortunio"
        assert not v.is_startable

    def test_squalificato_dichiarato_da_una_fonte(self):
        rosa = [player()]
        v = verdict_for(rosa, [
            report("A", entry("A", status=Status.STARTER)),
            report("B", entry("B", status=Status.OUT)),
        ])
        assert v.status is Status.OUT
        assert "indisponibile secondo B" in v.note


class TestSquadraNonInCampo:
    def test_squadra_a_riposo(self):
        rosa = [player()]
        v = verdict_for(
            rosa,
            [report("A", entry("A", status=Status.STARTER))],
            teams_playing={"Milan", "Inter"},
        )
        assert v.team_playing is False
        assert "non in campo" in v.note

    def test_squadra_in_campo(self):
        rosa = [player()]
        v = verdict_for(
            rosa,
            [report("A", entry("A", status=Status.STARTER))],
            teams_playing={"Genoa", "Como"},
        )
        assert v.team_playing is True


class TestPesiFonte:
    def test_fonte_con_peso_doppio_ribalta_la_maggioranza(self):
        rosa = [player()]
        aggregator = Aggregator(SETTINGS, source_weights={"A": 3.0, "B": 1.0, "C": 1.0})
        verdicts = aggregator.aggregate(rosa, [
            report("A", entry("A", status=Status.STARTER)),
            report("B", entry("B", status=Status.BENCH)),
            report("C", entry("C", status=Status.BENCH)),
        ])
        assert verdicts[0].status is Status.STARTER
        assert verdicts[0].vote == pytest.approx(0.6)


class TestProbabilita:
    def test_media_delle_percentuali(self):
        rosa = [player()]
        v = verdict_for(rosa, [
            report("A", entry("A", probability=90.0)),
            report("B", entry("B", probability=70.0)),
        ])
        assert v.probability == pytest.approx(80.0)


class TestTiebreakers:
    def test_ordine_dei_criteri(self):
        from fantabot.models import PlayerVerdict

        base = PlayerVerdict(player=player(order=3, fantamedia=7.5), status=Status.DOUBT,
                             vote=0.5, voting_weight=2, consensus=0.5, probability=65.0)
        assert resolve_doubt(base, ("probabilita_media", "fantamedia")) == 65.0
        # Senza probabilita' si scende al criterio successivo.
        base.probability = None
        assert resolve_doubt(base, ("probabilita_media", "fantamedia")) == 7.5
        # Ultimo criterio: chi e' piu' in alto in rosa vince (order piu' basso).
        assert resolve_doubt(base, ("ordine_rosa",)) == -3.0

    def test_titolarita_recente(self):
        from fantabot.models import PlayerVerdict

        v = PlayerVerdict(player=player(recent_appearances=4), status=Status.DOUBT,
                          vote=0.5, voting_weight=2, consensus=0.5)
        assert resolve_doubt(v, ("titolarita_recente",)) == 40.0


class TestNamingDiverso:
    def test_fonti_con_nomi_diversi_votano_lo_stesso_giocatore(self):
        rosa = [player(name="Vasquez")]
        v = verdict_for(rosa, [
            report("A", entry("A", name="Vasquez", status=Status.STARTER)),
            report("B", entry("B", name="Vásquez J.", status=Status.STARTER)),
        ])
        assert set(v.per_source) == {"A", "B"}
        assert v.vote == pytest.approx(1.0)
