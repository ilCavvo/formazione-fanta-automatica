"""Statistiche di Serie A: chi si affronta e come sta andando.

Le fixture sono fette di HTML vero preso dalle pagine live, non markup
inventato: se il sito cambia struttura questi test se ne accorgono.
"""

from __future__ import annotations

from fantabot.models import Match, Matchday, Role, RosterPlayer
from fantabot.stats import (
    StatsBook,
    TeamStats,
    build_form_guide,
    fetch_stats,
    parse_classifica,
    parse_statistiche,
)


class TestClassifica:
    def test_legge_gol_fatti_e_subiti(self, fixture_html):
        squadre = parse_classifica(fixture_html("classifica.html"))
        roma = squadre["roma"]
        assert roma.name == "Roma"
        assert roma.played == 3
        assert roma.goals_for == 10
        assert roma.goals_against == 1

    def test_legge_tutte_le_squadre(self, fixture_html):
        assert len(parse_classifica(fixture_html("classifica.html"))) == 20

    def test_il_widget_in_fondo_alla_pagina_non_azzera_i_dati(self, fixture_html):
        """La pagina ripete la classifica in un widget con le sole colonne
        posizione e punti. Leggendo anche quelle righe ogni squadra risultava a
        zero gol e zero partite, e l'avversario non spostava piu' niente."""
        html = fixture_html("classifica.html")
        assert html.count('data-name="Roma"') == 2, "la fixture deve contenere il widget"
        assert parse_classifica(html)["roma"].goals_for == 10

    def test_legge_la_forma_recente(self, fixture_html):
        squadre = parse_classifica(fixture_html("classifica.html"))
        assert squadre["roma"].form == ("W", "W", "W")
        assert squadre["roma"].form_points == 3.0

    def test_attacco_e_difesa_per_partita(self):
        t = TeamStats(name="X", played=4, goals_for=10, goals_against=2)
        assert t.attack == 2.5
        assert t.defence == 0.5

    def test_una_squadra_senza_partite_non_ha_medie(self):
        """Zero partite non vuol dire attacco a zero: vuol dire non si sa."""
        t = TeamStats(name="X", played=0, goals_for=0, goals_against=0)
        assert t.attack is None
        assert t.defence is None
        assert t.form_points is None


class TestStatisticheGiocatori:
    def test_legge_voti_e_bonus(self, fixture_html):
        giocatori = parse_statistiche(fixture_html("statistiche.html"))
        malen = next(g for g in giocatori if g.name == "Malen")
        assert malen.team == "ROM"
        assert malen.played == 3
        assert malen.media_voto == 7.5
        assert malen.fantamedia == 12.33
        assert malen.goals == 5
        assert malen.yellow_cards == 1

    def test_i_bonus_sono_la_differenza_fra_le_due_medie(self, fixture_html):
        """Non si ricostruiscono da gol e assist: sarebbe indovinare il
        regolamento della lega. La differenza fra fantamedia e media voto e'
        gia' il saldo, calcolato dal sito."""
        giocatori = parse_statistiche(fixture_html("statistiche.html"))
        malen = next(g for g in giocatori if g.name == "Malen")
        assert round(malen.bonus_per_match, 2) == 4.83

    def test_senza_voti_non_si_inventano_bonus(self):
        from fantabot.stats import PlayerStats

        assert PlayerStats(name="X", team="Y").bonus_per_match is None

    def test_legge_tutte_le_righe(self, fixture_html):
        assert len(parse_statistiche(fixture_html("statistiche.html"))) == 12


class TestMedieDiCampionato:
    """Il confronto e' sempre contro la media, non contro lo zero.

    Senza centrare, un avversario qualunque sposterebbe il punteggio di tutti
    nella stessa direzione invece di distinguere il facile dal difficile.
    """

    def test_media_attacco_e_difesa(self, fixture_html):
        book = StatsBook(teams=parse_classifica(fixture_html("classifica.html")))
        assert book.average_attack is not None
        assert 0.5 < book.average_attack < 3.0
        # In un campionato i gol fatti e quelli subiti si pareggiano.
        assert round(book.average_attack, 6) == round(book.average_defence, 6)

    def test_senza_squadre_non_c_e_media(self):
        assert StatsBook().average_attack is None


class TestFormGuide:
    def rosa(self):
        return [
            RosterPlayer(name="Malen", team="Roma", role=Role.A, order=0),
            RosterPlayer(name="Svilar", team="Roma", role=Role.P, order=1),
        ]

    def giornata(self):
        return Matchday(
            matchweek=4,
            matches=[Match(matchweek=4, home="Roma", away="Lazio", kickoff=None)],
            deadline=None,
        )

    def guida(self, fixture_html):
        book = StatsBook(
            teams=parse_classifica(fixture_html("classifica.html")),
            players=parse_statistiche(fixture_html("statistiche.html")),
        )
        return build_form_guide(book, matchday=self.giornata(), roster=self.rosa())

    def test_sa_chi_si_affronta_nei_due_versi(self, fixture_html):
        guide = self.guida(fixture_html)
        assert guide.opponent_name("Roma") == "Lazio"
        assert guide.opponent_name("Lazio") == "Roma"

    def test_l_avversario_porta_con_se_le_sue_statistiche(self, fixture_html):
        guide = self.guida(fixture_html)
        lazio = guide.opponent_of("Roma")
        assert lazio is not None and lazio.name == "Lazio"

    def test_appaia_il_giocatore_sciogliendo_la_sigla(self, fixture_html):
        """La pagina scrive `ROM`, la rosa `Roma`: senza scioglierla nessun
        giocatore verrebbe riconosciuto."""
        guide = self.guida(fixture_html)
        stat = guide.stats_for("Malen")
        assert stat is not None and stat.goals == 5

    def test_chi_non_e_nella_pagina_resta_senza(self, fixture_html):
        assert self.guida(fixture_html).stats_for("Nessuno") is None

    def test_senza_statistiche_la_guida_e_vuota_ma_usabile(self):
        guide = build_form_guide(StatsBook())
        assert not guide
        assert guide.stats_for("Malen") is None
        assert guide.opponent_of("Roma") is None


class FintaRisposta:
    def __init__(self, text):
        self.text = text


class TestScaricamento:
    """Una statistica che manca non e' un errore: e' un contributo in meno."""

    def test_un_guasto_non_ferma_il_run(self, fixture_html):
        class Rotto:
            def get(self, url):
                if "classifica" in url:
                    return FintaRisposta(fixture_html("classifica.html"))
                raise RuntimeError("502")

        book = fetch_stats(Rotto())
        assert book.teams and not book.players
        assert any("502" in e for e in book.errors)

    def test_una_pagina_vuota_viene_segnalata(self):
        class Vuoto:
            def get(self, url):
                return FintaRisposta("<html><body></body></html>")

        book = fetch_stats(Vuoto())
        assert not book
        assert len(book.errors) == 2

    def test_due_sole_richieste(self, fixture_html):
        """Il brief chiede scraping gentile: due pagine per run, non una per
        giocatore."""
        chiamate = []

        class Client:
            def get(self, url):
                chiamate.append(url)
                nome = "classifica.html" if "classifica" in url else "statistiche.html"
                return FintaRisposta(fixture_html(nome))

        fetch_stats(Client())
        assert len(chiamate) == 2
