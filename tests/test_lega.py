"""Logica pura del client della lega (nessun browser).

Il grosso di `LeagueClient` ha bisogno di Playwright e di un account vero, ma
le due decisioni che hanno fatto fallire i primi run reali sono funzioni pure e
qui vengono fissate: riconoscere il muro di login, e non far uscire i valori
dei cookie dal report.
"""

from __future__ import annotations

import pytest

from fantabot.lega.client import is_login_url
from fantabot.lega.discovery import PageSummary, to_markdown

LEAGUE = "https://leghe.fantacalcio.it/fantasantos-2022-2023"


class TestRiconoscimentoLogin:
    @pytest.mark.parametrize(
        "url",
        [
            # Il caso reale: la pagina lineup rimbalza qui quando la sessione
            # vale su www ma non su leghe.
            "https://leghe.fantacalcio.it/login?next=%2Fx%2Fview%2Fcompetition%2Flineup",
            "https://www.fantacalcio.it/login",
            "https://leghe.fantacalcio.it/login/",
            "https://www.fantacalcio.it/accedi",
        ],
    )
    def test_riconosce_le_pagine_di_login(self, url):
        assert is_login_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            f"{LEAGUE}/view/competition/301229/lineup",
            f"{LEAGUE}/view/rosters/4163261",
            LEAGUE,
        ],
    )
    def test_le_pagine_normali_non_sono_login(self, url):
        assert is_login_url(url) is False

    def test_un_parametro_next_non_basta_a_far_scattare_il_muro(self):
        """Solo il percorso conta: `?next=/login` non e' una pagina di login."""
        assert is_login_url(f"{LEAGUE}/view/competition/lineup?next=/login") is False

    def test_url_vuoto(self):
        assert is_login_url("") is False


class TestSezioneSessioneDelReport:
    def _report(self, cookies, final_url=f"{LEAGUE}/view/competition/301229/lineup"):
        summary = PageSummary(
            name="formazione",
            requested_url=f"{LEAGUE}/view/competition/lineup",
            final_url=final_url,
        )
        return to_markdown([summary], cookies=cookies)

    def test_elenca_i_domini_dei_cookie(self):
        report = self._report([("www.fantacalcio.it", "SESSID"),
                               ("leghe.fantacalcio.it", "PHPSESSID")])
        assert "www.fantacalcio.it" in report
        assert "leghe.fantacalcio.it" in report
        assert "PHPSESSID" in report

    def test_non_stampa_mai_i_valori_dei_cookie(self):
        """I valori sono credenziali a tutti gli effetti: il report e' pubblicabile."""
        report = self._report([("www.fantacalcio.it", "SESSID")])
        assert "SESSID" in report          # il nome si', serve a diagnosticare
        assert "valori" in report          # ed e' detto esplicitamente

    def test_segnala_le_pagine_rimbalzate_sul_login(self):
        report = self._report(
            [("www.fantacalcio.it", "SESSID")],
            final_url="https://leghe.fantacalcio.it/login?next=%2Fx",
        )
        assert "rimbalzate sul login" in report
        assert "formazione" in report

    def test_nessun_rimbalzo_nessuna_segnalazione(self):
        report = self._report([("leghe.fantacalcio.it", "PHPSESSID")])
        assert "rimbalzate sul login" not in report

    def test_sessione_assente_e_detta_chiaramente(self):
        report = self._report([])
        assert "Nessun cookie" in report

    def test_senza_cookie_la_sezione_non_compare(self):
        """`to_markdown` resta usabile anche senza diagnostica di sessione."""
        summary = PageSummary(name="x", requested_url=LEAGUE, final_url=LEAGUE)
        assert "## Sessione" not in to_markdown([summary])


class TestSelettoriLogin:
    def test_candidati_dal_piu_specifico_al_piu_generico(self, selectors):
        """Il form puo' stare su domini diversi, con markup diverso."""
        login = selectors["login"]
        assert login["password_input"][-1] == "input[type=password]"
        assert len(login["username_input"]) >= 3
        assert len(login["submit_button"]) >= 3
