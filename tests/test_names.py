"""Matching dei nomi fra fonti che li scrivono in modo diverso."""

from __future__ import annotations

import pytest

from fantabot.names import AliasMap, PlayerMatcher, initial_of, normalize, surname_of


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Vásquez", "vasquez"),
        ("Dodô", "dodo"),
        ("D'Ambrosio", "d ambrosio"),
        ("  Bijlow  ", "bijlow"),
        ("Thuram M.", "thuram m"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_surname_toglie_iniziale_puntata():
    assert surname_of("Bijlow J.") == "bijlow"
    assert surname_of("Ostigard L.") == "ostigard"
    # Senza iniziale puntata il nome resta intero.
    assert surname_of("Justin Bijlow") == "justin bijlow"


def test_initial_of():
    assert initial_of("Thuram M.") == "m"
    assert initial_of("Thuram") is None


class TestPlayerMatcher:
    roster = [("Bijlow", "Genoa"), ("Vasquez", "Genoa"), ("Lautaro", "Inter")]

    def test_match_esatto(self):
        matcher = PlayerMatcher(self.roster)
        assert matcher.match("Bijlow", "Genoa") == ("Bijlow", "Genoa")

    def test_match_con_iniziale_sky(self):
        matcher = PlayerMatcher(self.roster)
        assert matcher.match("Bijlow J.", "Genoa") == ("Bijlow", "Genoa")

    def test_match_con_accento(self):
        matcher = PlayerMatcher(self.roster)
        assert matcher.match("Vásquez J.", "Genoa") == ("Vasquez", "Genoa")

    def test_match_con_nome_completo(self):
        matcher = PlayerMatcher(self.roster)
        assert matcher.match("Justin Bijlow", "Genoa") == ("Bijlow", "Genoa")

    def test_squadra_diversa_non_matcha(self):
        """Due omonimi in squadre diverse non devono mai confondersi."""
        matcher = PlayerMatcher(self.roster, require_same_team=True)
        assert matcher.match("Bijlow", "Como") is None

    def test_giocatore_sconosciuto(self):
        matcher = PlayerMatcher(self.roster)
        assert matcher.match("Cristiano Ronaldo", "Genoa") is None

    def test_alias_manuale_risolve_omonimi(self):
        aliases = AliasMap(
            players={"marcus thuram": "thuram m", "thuram m": "thuram m"},
            teams={"internazionale": "inter", "inter": "inter"},
        )
        matcher = PlayerMatcher([("Thuram M.", "Inter")], aliases=aliases)
        assert matcher.match("Marcus Thuram", "Internazionale") == ("Thuram M.", "Inter")


def test_alias_map_load_dal_repo(tmp_path):
    path = tmp_path / "aliases.yaml"
    path.write_text(
        'players:\n  "Thuram M.": ["Marcus Thuram"]\nteams:\n  "Inter": ["Internazionale"]\n',
        encoding="utf-8",
    )
    aliases = AliasMap.load(path)
    assert aliases.canonical_player("Marcus Thuram") == "thuram m"
    assert aliases.canonical_team("Internazionale") == "inter"


def test_alias_map_file_mancante_non_esplode():
    assert AliasMap.load("/percorso/che/non/esiste.yaml").players == {}
