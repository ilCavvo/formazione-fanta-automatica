"""Logica di formazione: e' la parte con piu' regole, quindi la piu' testata.

I test costruiscono rose sintetiche in cui la risposta giusta e' evidente, e
verificano che modulo, titolari e panchina siano quelli attesi.
"""

from __future__ import annotations

import pytest

from fantabot.lineup import LineupError, LineupSettings, Module, build_lineup, score_player
from fantabot.models import PlayerVerdict, Role, RosterPlayer, Status


def make_verdict(
    name: str,
    role: Role,
    status: Status = Status.STARTER,
    *,
    team: str = "Genoa",
    order: int = 0,
    probability: float | None = None,
    consensus: float = 1.0,
    fantamedia: float | None = None,
    team_playing: bool = True,
    out_reason: str | None = None,
) -> PlayerVerdict:
    return PlayerVerdict(
        player=RosterPlayer(name=name, team=team, role=role, order=order,
                            fantamedia=fantamedia),
        status=status,
        vote=1.0 if status is Status.STARTER else 0.5,
        voting_weight=3.0,
        consensus=consensus,
        probability=probability,
        team_playing=team_playing,
        out_reason=out_reason,
    )


def rosa(
    n_p: int = 3, n_d: int = 8, n_c: int = 8, n_a: int = 6,
    status: Status = Status.STARTER,
) -> list[PlayerVerdict]:
    """Rosa standard da 25: tutti titolari, cosi' i test isolano una variabile."""
    out: list[PlayerVerdict] = []
    for role, count in ((Role.P, n_p), (Role.D, n_d), (Role.C, n_c), (Role.A, n_a)):
        for i in range(count):
            out.append(make_verdict(f"{role.value}{i}", role, status, order=len(out)))
    return out


def settings(**kw) -> LineupSettings:
    base = {
        "modules": ("3-4-3", "3-5-2", "4-3-3", "4-4-2", "4-5-1", "5-3-2", "5-4-1"),
        "module_preferences": {},
        "slot_limits": {"D": (3, 5), "C": (3, 5), "A": (1, 3)},
        "bench_size": 14,
    }
    base.update(kw)
    return LineupSettings(**base)


# --------------------------------------------------------------------------


class TestModule:
    def test_parse(self):
        m = Module.parse("4-3-3")
        assert (m.difensori, m.centrocampisti, m.attaccanti) == (4, 3, 3)
        assert sum(m.slots().values()) == 11

    def test_modulo_non_valido(self):
        with pytest.raises(LineupError):
            Module.parse("4-4-4")  # farebbe 13 titolari

    def test_modulo_malformato(self):
        with pytest.raises(LineupError):
            Module.parse("quattro-quattro-due")


class TestScorePlayer:
    def test_titolare_vale_piu_di_dubbio_e_panchina(self):
        s = settings()
        titolare = score_player(make_verdict("A", Role.A, Status.STARTER), s)
        dubbio = score_player(make_verdict("B", Role.A, Status.DOUBT), s)
        panchina = score_player(make_verdict("C", Role.A, Status.BENCH), s)
        assert titolare.score > dubbio.score > panchina.score

    def test_probabilita_alta_batte_probabilita_bassa(self):
        s = settings()
        alto = score_player(make_verdict("A", Role.A, probability=95.0), s)
        basso = score_player(make_verdict("B", Role.A, probability=55.0), s)
        assert alto.score > basso.score

    def test_squadra_ferma_affonda_il_punteggio(self):
        s = settings(pen_squadra_ferma=500.0)
        fermo = score_player(make_verdict("A", Role.A, team_playing=False), s)
        gioca = score_player(make_verdict("B", Role.A), s)
        assert fermo.score < gioca.score - 400

    def test_ordine_rosa_come_spareggio(self):
        s = settings()
        primo = score_player(make_verdict("A", Role.A, order=0), s)
        ultimo = score_player(make_verdict("B", Role.A, order=20), s)
        assert primo.score > ultimo.score

    def test_breakdown_e_ispezionabile(self):
        s = settings()
        scored = score_player(make_verdict("A", Role.A, probability=90.0), s)
        assert "stato" in scored.breakdown
        assert scored.breakdown["probabilita"] == pytest.approx(0.9 * s.w_probabilita)


class TestModuloDinamico:
    def test_undici_titolari_e_un_portiere(self):
        lineup = build_lineup(rosa(), settings())
        assert len(lineup.starters) == 11
        assert len(lineup.by_role(Role.P)) == 1

    def test_il_modulo_segue_i_titolari_disponibili(self):
        """Con 5 difensori titolari e solo 1 attaccante, deve andare sul 5-4-1."""
        verdicts = (
            [make_verdict("P0", Role.P)]
            + [make_verdict(f"D{i}", Role.D) for i in range(5)]
            + [make_verdict(f"C{i}", Role.C) for i in range(4)]
            + [make_verdict("A0", Role.A)]
            + [make_verdict(f"Db{i}", Role.D, Status.BENCH) for i in range(2)]
            + [make_verdict(f"Ab{i}", Role.A, Status.BENCH) for i in range(3)]
        )
        lineup = build_lineup(verdicts, settings())
        assert lineup.module == "5-4-1"

    def test_evita_di_schierare_chi_e_dato_in_panchina(self):
        verdicts = (
            [make_verdict("P0", Role.P)]
            + [make_verdict(f"D{i}", Role.D) for i in range(3)]
            + [make_verdict(f"C{i}", Role.C) for i in range(5)]
            + [make_verdict(f"A{i}", Role.A) for i in range(2)]
            + [make_verdict(f"Dpanca{i}", Role.D, Status.BENCH) for i in range(4)]
        )
        lineup = build_lineup(verdicts, settings())
        assert lineup.module == "3-5-2"
        assert all("panca" not in p.player.name for p in lineup.starters)

    def test_malus_dubbi_scoraggia_i_moduli_rischiosi(self):
        """A parita' di tutto, preferisce il modulo che schiera meno dubbi."""
        verdicts = (
            [make_verdict("P0", Role.P)]
            + [make_verdict(f"D{i}", Role.D) for i in range(3)]
            + [make_verdict(f"C{i}", Role.C) for i in range(5)]
            + [make_verdict(f"A{i}", Role.A) for i in range(2)]
            # Quarto difensore solo "dubbio": passare al 4-4-2 costerebbe.
            + [make_verdict("Ddubbio", Role.D, Status.DOUBT)]
            + [make_verdict("Cpanca", Role.C, Status.BENCH)]
        )
        lineup = build_lineup(verdicts, settings(pen_dubbio=50.0))
        assert lineup.module == "3-5-2"
        assert "Ddubbio" not in [p.player.name for p in lineup.starters]

    def test_solo_i_moduli_ammessi(self):
        lineup = build_lineup(rosa(), settings(modules=("3-5-2",)))
        assert lineup.module == "3-5-2"
        assert set(lineup.module_scores) == {"3-5-2"}

    def test_modulo_fuori_dai_limiti_di_reparto_viene_scartato(self):
        s = settings(modules=("3-5-2", "5-3-2"), slot_limits={"D": (3, 4), "C": (3, 5),
                                                              "A": (1, 3)})
        lineup = build_lineup(rosa(), s)
        assert "5-3-2" not in lineup.module_scores

    def test_nessun_modulo_utilizzabile(self):
        s = settings(modules=("6-3-1",))
        with pytest.raises(LineupError):
            build_lineup(rosa(), s)


class TestModificatoreDifesa:
    def test_con_modificatore_preferisce_la_difesa_a_4(self):
        """Stessa rosa, unico cambio: il modificatore difesa attivo."""
        verdicts = rosa()
        senza = build_lineup(verdicts, settings(modificatore_difesa=False))
        con = build_lineup(verdicts, settings(modificatore_difesa=True,
                                              bonus_difesa_4=60.0, bonus_difesa_5=25.0))
        assert len(con.by_role(Role.D)) == 4
        assert con.module.startswith("4-")
        # Il bonus deve aver cambiato davvero qualcosa rispetto al caso base.
        assert senza.module != con.module or len(senza.by_role(Role.D)) == 4

    def test_bonus_difesa_5_se_i_difensori_sono_i_migliori(self):
        s = settings(modificatore_difesa=True, bonus_difesa_4=10.0, bonus_difesa_5=200.0)
        lineup = build_lineup(rosa(), s)
        assert len(lineup.by_role(Role.D)) == 5

    def test_senza_modificatore_nessun_bonus_difesa(self):
        s = settings(modificatore_difesa=False, bonus_difesa_4=999.0)
        lineup = build_lineup(rosa(), s)
        assert lineup.module_scores  # il bonus non e' stato applicato
        # Con tutti uguali, i moduli si equivalgono: nessuno domina per 999 punti.
        scores = sorted(lineup.module_scores.values(), reverse=True)
        assert scores[0] - scores[-1] < 100

    def test_preferenza_esplicita_rompe_la_parita(self):
        s = settings(module_preferences={"4-4-2": 500.0})
        lineup = build_lineup(rosa(), s)
        assert lineup.module == "4-4-2"


class TestIndisponibili:
    def test_infortunato_non_viene_mai_schierato(self):
        verdicts = rosa()
        verdicts[3].status = Status.OUT  # un difensore
        verdicts[3].out_reason = "infortunio"
        lineup = build_lineup(verdicts, settings())
        assert verdicts[3].player.name not in [p.player.name for p in lineup.starters]
        assert verdicts[3].player.name not in [p.player.name for p in lineup.bench]

    def test_sostituzione_registrata_nelle_decisioni(self):
        verdicts = rosa()
        verdicts[3].status = Status.OUT
        verdicts[3].out_reason = "squalifica"
        lineup = build_lineup(verdicts, settings())
        testo = " ".join(lineup.decisions)
        assert verdicts[3].player.name in testo
        assert "squalifica" in testo

    def test_tutti_i_portieri_out_lascia_rosa_incompleta(self):
        verdicts = rosa(n_p=1)
        verdicts[0].status = Status.OUT
        lineup = build_lineup(verdicts, settings(allow_incomplete=True))
        assert lineup.by_role(Role.P) == []
        assert any("incompleta" in w for w in lineup.warnings)

    def test_rosa_incompleta_vietata(self):
        verdicts = rosa(n_p=1)
        verdicts[0].status = Status.OUT
        with pytest.raises(LineupError):
            build_lineup(verdicts, settings(allow_incomplete=False))


class TestPanchina:
    def test_panchina_ordinata_per_ruolo(self):
        lineup = build_lineup(rosa(), settings(bench_strategy="per_ruolo_poi_punteggio"))
        ordine = [p.role for p in lineup.bench]
        indici = [[Role.P, Role.D, Role.C, Role.A].index(r) for r in ordine]
        assert indici == sorted(indici)

    def test_panchina_solo_punteggio(self):
        lineup = build_lineup(rosa(), settings(bench_strategy="solo_punteggio"))
        punteggi = [p.score for p in lineup.bench]
        assert punteggi == sorted(punteggi, reverse=True)

    def test_titolari_e_panchina_non_si_sovrappongono(self):
        lineup = build_lineup(rosa(), settings())
        titolari = {p.player.name for p in lineup.starters}
        panchina = {p.player.name for p in lineup.bench}
        assert titolari & panchina == set()

    def test_panchina_rispetta_la_dimensione_massima(self):
        lineup = build_lineup(rosa(), settings(bench_size=5))
        assert len(lineup.bench) == 5


class TestDecisioni:
    def test_dubbio_schierato_viene_spiegato(self):
        verdicts = (
            [make_verdict("P0", Role.P)]
            + [make_verdict(f"D{i}", Role.D) for i in range(3)]
            + [make_verdict(f"C{i}", Role.C) for i in range(5)]
            + [make_verdict("A0", Role.A)]
            + [make_verdict("Adubbio", Role.A, Status.DOUBT)]
        )
        verdicts[-1].note = "nessun consenso chiaro"
        lineup = build_lineup(verdicts, settings())
        assert any("Dubbio risolto" in d and "Adubbio" in d for d in lineup.decisions)

    def test_confronto_fra_moduli_nelle_decisioni(self):
        lineup = build_lineup(rosa(), settings())
        assert any("Modulo" in d and "scelto su" in d for d in lineup.decisions)

    def test_avviso_squadra_non_in_campo(self):
        verdicts = rosa()
        verdicts[0].team_playing = False  # il portiere titolare
        lineup = build_lineup(verdicts, settings(pen_squadra_ferma=0.0))
        assert any("non risulta in campo" in d for d in lineup.decisions)


class TestConfigReale:
    """La config del repo deve produrre una formazione valida senza modifiche."""

    def test_settings_dalla_config_versionata(self, real_config):
        s = LineupSettings.from_config(real_config)
        assert len(s.modules) == 7
        assert s.slot_limits["D"] == (3, 5)
        lineup = build_lineup(rosa(), s)
        assert len(lineup.starters) == 11

    def test_flag_modificatore_difesa_dalla_config(self, config_factory):
        cfg = config_factory({"league.modifiers.modificatore_difesa": True})
        lineup = build_lineup(rosa(), LineupSettings.from_config(cfg))
        assert len(lineup.by_role(Role.D)) == 4


# --------------------------------------------------------------------------
# Chi si affronta e come sta andando
# --------------------------------------------------------------------------


def guida(
    *,
    avversari: dict[str, str] | None = None,
    squadre: dict[str, tuple[int, int, int]] | None = None,
    giocatori: dict[str, tuple[float, float, int]] | None = None,
):
    """Costruisce una `FormGuide` sintetica.

    `squadre`: nome -> (partite, gol fatti, gol subiti).
    `giocatori`: nome -> (media voto, fantamedia, partite).
    """
    from fantabot.stats import (
        PlayerStats,
        StatsBook,
        TeamStats,
        build_form_guide,
    )

    book = StatsBook(teams={
        nome.casefold(): TeamStats(name=nome, played=pg, goals_for=gf, goals_against=gs)
        for nome, (pg, gf, gs) in (squadre or {}).items()
    })
    guide = build_form_guide(book)
    guide.opponents = {k.casefold(): v for k, v in (avversari or {}).items()}
    guide.by_player = {
        nome: PlayerStats(name=nome, team="", played=pg, media_voto=mv, fantamedia=fm)
        for nome, (mv, fm, pg) in (giocatori or {}).items()
    }
    return guide


#: Due avversari agli antipodi, in un campionato che segna 1 gol a partita.
CAMPIONATO = {
    "Genoa": (10, 10, 10),
    "Corazzata": (10, 25, 2),    # segna tantissimo, subisce pochissimo
    "Materasso": (10, 2, 25),    # segna pochissimo, subisce tantissimo
}


class TestAvversarioDelPortiere:
    """La regola chiesta: avversario che fa tanti gol, portiere che vale meno."""

    def _portiere_contro(self, avversario: str) -> float:
        s = settings()
        g = guida(avversari={"Genoa": avversario}, squadre=CAMPIONATO)
        return score_player(make_verdict("Portiere", Role.P), s, g).score

    def test_affrontare_chi_segna_tanto_abbassa_il_punteggio(self):
        assert self._portiere_contro("Corazzata") < self._portiere_contro("Materasso")

    def test_il_divario_e_sensibile(self):
        divario = self._portiere_contro("Materasso") - self._portiere_contro("Corazzata")
        # Due portieri altrimenti identici devono separarsi in modo netto,
        # altrimenti la regola c'e' ma non decide niente.
        assert divario > 10

    def test_pesa_piu_sul_portiere_che_sugli_altri(self):
        """"In maniera minore" per gli altri ruoli, non uguale."""
        s = settings()
        forte = guida(avversari={"Genoa": "Corazzata"}, squadre=CAMPIONATO)
        debole = guida(avversari={"Genoa": "Materasso"}, squadre=CAMPIONATO)

        def divario(role):
            return (score_player(make_verdict("X", role), s, debole).score
                    - score_player(make_verdict("X", role), s, forte).score)

        assert divario(Role.P) > divario(Role.D) > 0

    def test_a_parita_di_probabili_l_avversario_sceglie_il_portiere(self):
        """Il caso vero: due portieri titolari, decide chi affrontano."""
        s = settings()
        g = guida(avversari={"Casa": "Materasso", "Trasferta": "Corazzata"},
                  squadre={**CAMPIONATO, "Casa": (10, 10, 10), "Trasferta": (10, 10, 10)})
        facile = make_verdict("Facile", Role.P, team="Casa")
        difficile = make_verdict("Difficile", Role.P, team="Trasferta")
        lineup = build_lineup([facile, difficile] + rosa(n_p=0), s, g)
        assert lineup.by_role(Role.P)[0].player.name == "Facile"


class TestAvversarioDegliAltriRuoli:
    """Per chi attacca, "avversario forte" vuol dire difesa che non prende gol."""

    def test_una_difesa_che_incassa_apre_al_bonus(self):
        s = settings()
        colabrodo = guida(avversari={"Genoa": "Materasso"}, squadre=CAMPIONATO)
        blindata = guida(avversari={"Genoa": "Corazzata"}, squadre=CAMPIONATO)
        contro_materasso = score_player(make_verdict("A", Role.A), s, colabrodo).score
        contro_corazzata = score_player(make_verdict("A", Role.A), s, blindata).score
        assert contro_materasso > contro_corazzata


class TestQuandoLeStatisticheNonSiUsano:
    def test_senza_guida_il_punteggio_resta_quello_di_prima(self):
        s = settings()
        assert "avversario" not in score_player(make_verdict("X", Role.P), s).breakdown

    def test_poche_partite_non_fanno_statistica(self):
        """Una vittoria per 5-0 alla prima non rende una squadra imbattibile."""
        s = settings(min_partite=3)
        g = guida(avversari={"Genoa": "Corazzata"},
                  squadre={"Genoa": (1, 1, 1), "Corazzata": (1, 5, 0)})
        assert "avversario" not in score_player(make_verdict("X", Role.P), s, g).breakdown

    def test_un_avversario_ignoto_non_sposta_niente(self):
        s = settings()
        g = guida(avversari={"Genoa": "Sconosciuta"}, squadre=CAMPIONATO)
        assert "avversario" not in score_player(make_verdict("X", Role.P), s, g).breakdown

    def test_lo_scarto_e_limitato(self):
        """Senza tetto, tre giornate anomale deciderebbero la formazione."""
        s = settings(max_scarto_avversario=0.5)
        g = guida(avversari={"Genoa": "Corazzata"}, squadre=CAMPIONATO)
        peso = score_player(make_verdict("X", Role.P), s, g).breakdown["avversario"]
        assert abs(peso) <= 0.5 * s.w_avversario["P"] + 1e-9


class TestVotiEBonus:
    def test_chi_prende_voti_alti_vale_di_piu(self):
        s = settings()
        g = guida(giocatori={"Alto": (7.0, 7.0, 5), "Basso": (5.0, 5.0, 5)})
        assert (score_player(make_verdict("Alto", Role.C), s, g).score
                > score_player(make_verdict("Basso", Role.C), s, g).score)

    def test_a_parita_di_voto_decidono_i_bonus(self):
        s = settings()
        g = guida(giocatori={"Bomber": (6.0, 8.0, 5), "Compitino": (6.0, 6.0, 5)})
        assert (score_player(make_verdict("Bomber", Role.C), s, g).score
                > score_player(make_verdict("Compitino", Role.C), s, g).score)

    def test_i_malus_pesano_in_negativo(self):
        s = settings()
        g = guida(giocatori={"Falloso": (6.0, 5.0, 5)})
        assert score_player(make_verdict("Falloso", Role.C), s, g).breakdown["bonus"] < 0

    def test_il_sei_e_il_punto_neutro(self):
        s = settings()
        g = guida(giocatori={"Sufficiente": (6.0, 6.0, 5)})
        breakdown = score_player(make_verdict("Sufficiente", Role.C), s, g).breakdown
        assert breakdown["media_voto"] == 0.0

    def test_poche_partite_non_fanno_media(self):
        s = settings(min_partite=3)
        g = guida(giocatori={"Esordiente": (9.0, 15.0, 1)})
        assert "bonus" not in score_player(make_verdict("Esordiente", Role.C), s, g).breakdown

    def test_una_giornata_irripetibile_non_domina(self):
        s = settings(max_scarto_bonus=1.0)
        g = guida(giocatori={"Tripletta": (6.0, 15.0, 3)})
        peso = score_player(make_verdict("Tripletta", Role.C), s, g).breakdown["bonus"]
        assert peso == pytest.approx(1.0 * s.w_bonus)


class TestPanchinaPerPunteggio:
    """Ordine chiesto: punteggio decrescente, portieri sempre in fondo."""

    def test_e_il_comportamento_predefinito(self):
        assert LineupSettings().bench_strategy == "punteggio_portieri_in_fondo"

    def test_i_portieri_stanno_in_fondo(self):
        lineup = build_lineup(rosa(), settings())
        ruoli = [p.role for p in lineup.bench]
        portieri = [i for i, r in enumerate(ruoli) if r is Role.P]
        assert portieri, "senza portieri in panchina il test non prova niente"
        assert min(portieri) > max(i for i, r in enumerate(ruoli) if r is not Role.P)

    def test_gli_altri_sono_in_ordine_di_punteggio(self):
        lineup = build_lineup(rosa(), settings())
        punteggi = [p.score for p in lineup.bench if p.role is not Role.P]
        assert punteggi == sorted(punteggi, reverse=True)

    def test_anche_i_portieri_fra_loro_sono_ordinati(self):
        lineup = build_lineup(rosa(), settings())
        punteggi = [p.score for p in lineup.bench if p.role is Role.P]
        assert punteggi == sorted(punteggi, reverse=True)


class TestQualitaControTitolarita:
    """Fin dove le statistiche possono ribaltare le probabili.

    E' la domanda che decide quanto ci fidiamo delle probabili, ed e'
    documentata in `docs/punteggi.md`: qui la si fissa perche' non cambi per
    sbaglio ritoccando un peso.
    """

    def scenario(self, status, prob):
        s = settings()
        g = guida(
            avversari={"Forte": "Materasso", "Media": "Genoa"},
            squadre={**CAMPIONATO, "Forte": (10, 10, 10), "Media": (10, 10, 10)},
            giocatori={"Campione": (7.4, 10.0, 10), "Scarso": (5.5, 5.0, 10)},
        )
        campione = score_player(
            make_verdict("Campione", Role.A, status, team="Forte", probability=prob), s, g
        ).score
        scarso = score_player(
            make_verdict("Scarso", Role.A, Status.STARTER, team="Media", probability=85.0), s, g
        ).score
        return campione, scarso

    def test_un_campione_in_dubbio_batte_un_titolare_scarso(self):
        """Se le fonti non sono sicure, decide la qualita'."""
        campione, scarso = self.scenario(Status.DOUBT, 50.0)
        assert campione > scarso

    def test_un_campione_dato_in_panchina_no(self):
        """"Non gioca" e' un'informazione, non un dubbio: non si scavalca."""
        campione, scarso = self.scenario(Status.BENCH, 20.0)
        assert campione < scarso

    def test_nessun_tetto_artificiale_lo_impedisce(self):
        """Il limite viene dai pesi, non da un massimo imposto a mano."""
        assert LineupSettings().max_totale_statistiche == 0.0

    def test_un_indisponibile_resta_fuori_qualunque_statistica_abbia(self):
        """L'unica regola assoluta: gli infortunati non si schierano."""
        s = settings()
        g = guida(giocatori={"Campione": (7.4, 10.0, 10)})
        fenomeno = make_verdict("Campione", Role.A, Status.OUT, out_reason="infortunio")
        lineup = build_lineup([fenomeno] + rosa(), s, g)
        assert "Campione" not in [p.player.name for p in lineup.starters]
        assert "Campione" not in [p.player.name for p in lineup.bench]


class TestLaTabellaResistaAiRinomini:
    """`docs/punteggi.md` nomina le chiavi di config: se una sparisce, la
    documentazione mente e nessuno se ne accorge finche' non serve."""

    def test_le_chiavi_citate_esistono_davvero(self, real_config):
        import pathlib
        import re

        doc = pathlib.Path(__file__).resolve().parents[1] / "docs" / "punteggi.md"
        testo = doc.read_text(encoding="utf-8")
        citate = set(re.findall(r"`((?:scores|weights|penalties|module_bonus)\.[\w.]+)`", testo))
        assert citate, "la tabella deve citare le chiavi di config"

        mancanti = [
            chiave for chiave in sorted(citate)
            if real_config.get(f"lineup.{chiave}", None) is None
        ]
        assert mancanti == [], f"chiavi citate ma assenti da config.yaml: {mancanti}"
