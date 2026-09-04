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
