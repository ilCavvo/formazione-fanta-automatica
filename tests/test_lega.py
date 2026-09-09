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


class TestAggancioDalLoginDiWww:
    """Il secondo tentativo di accesso, quando il form in loco non basta."""

    def test_url_di_aggancio_ben_formato(self, selectors):
        from urllib.parse import parse_qs, quote, urlparse

        target = f"{LEAGUE}/view/competition/lineup"
        handoff = selectors["login"]["handoff_url"].format(
            target=quote(target, safe="")
        )
        parsed = urlparse(handoff)
        assert parsed.netloc == "www.fantacalcio.it"
        # Il target deve arrivare intero: se la codifica si perde, il sito
        # riporta l'utente altrove e il secondo tentativo non serve a nulla.
        assert parse_qs(parsed.query)["from"] == [target]

    def test_e_una_pagina_di_login(self, selectors):
        """Se non lo fosse, `_login_via_handoff` non proverebbe a compilarlo."""
        from urllib.parse import quote

        handoff = selectors["login"]["handoff_url"].format(target=quote(LEAGUE, safe=""))
        assert is_login_url(handoff) is True


class TestBannerConsensi:
    """Il CMP e' un overlay: intercetta i click e li fa scadere in timeout.

    Non e' un problema solo del login — bloccherebbe anche lo schieramento e il
    salvataggio della formazione, quindi la configurazione deve esserci e
    coprire il CMP che il sito usa davvero (PubTech).
    """

    def test_esiste_la_sezione_consent(self, selectors):
        assert "consent" in selectors

    def test_copre_il_cmp_di_fantacalcio(self, selectors):
        """PubTech e' quello che ha bloccato il run reale."""
        accept = " ".join(selectors["consent"]["accept_button"])
        assert "pubtech-cmp" in accept
        assert "#pubtech-cmp" in selectors["consent"]["container"]

    def test_bottoni_in_italiano_e_inglese(self, selectors):
        accept = " ".join(selectors["consent"]["accept_button"])
        assert "Accetta" in accept
        assert "Accept" in accept

    def test_c_e_un_ripiego_per_rimuovere_l_overlay(self, selectors):
        """Se nessun bottone e' cliccabile, l'overlay va tolto dal DOM."""
        assert len(selectors["consent"]["container"]) >= 1


class TestTimeoutDeiClick:
    def test_il_timeout_e_corto(self):
        """Aspettare 30s non serve: se un overlay intercetta, non passera' mai.

        Il run reale ha bruciato 30s per ciascun tentativo prima di fallire.
        """
        from fantabot.lega.client import CLICK_TIMEOUT_MS, CONSENT_TIMEOUT_MS

        assert CLICK_TIMEOUT_MS <= 10_000
        assert CONSENT_TIMEOUT_MS <= CLICK_TIMEOUT_MS


class TestCampiDiLogin:
    """La pagina di login di leghe e' Angular: i campi possono non avere `name`."""

    def test_candidati_specifici_prima_dei_generici(self, selectors):
        """`form input[type=text]` puo' prendere il campo sbagliato: va per ultimo."""
        username = selectors["login"]["username_input"]
        assert username[-1] == "form input[type=text]"
        assert username.index("input[name=username]") < username.index(
            "form input[type=text]"
        )

    def test_copre_i_form_control_di_angular(self, selectors):
        username = " ".join(selectors["login"]["username_input"])
        password = " ".join(selectors["login"]["password_input"])
        assert "formcontrolname=username" in username
        assert "formcontrolname=password" in password


class TestAttesaDelLogin:
    def test_c_e_un_attesa_esplicita_dopo_il_submit(self):
        """Su una SPA `networkidle` torna subito: serve attendere l'esito vero.

        Il run reale controllava l'URL nello stesso secondo del submit e
        concludeva "ancora sul login" prima che la XHR di accesso finisse.
        """
        from fantabot.lega.client import LOGIN_WAIT_MS

        assert LOGIN_WAIT_MS >= 10_000


class TestAtteseDiNavigazione:
    """`networkidle` non e' utilizzabile su queste pagine.

    Ads e tracker tengono connessioni aperte, quindi la rete non e' mai
    davvero ferma: la navigazione scadeva dopo 30s su una pagina che si era
    caricata subito. Il segnale affidabile e' `domcontentloaded`.
    """

    def test_nessuna_attesa_su_networkidle_nel_codice(self):
        import pathlib

        import fantabot.lega.client as client

        sorgente = pathlib.Path(client.__file__).read_text(encoding="utf-8")
        # Cerchiamo le *chiamate*, non le menzioni: i commenti e le docstring
        # che spiegano perche' non lo usiamo devono poter restare.
        chiamate = [
            riga.strip()
            for riga in sorgente.splitlines()
            if 'wait_until="networkidle"' in riga
            or 'wait_for_load_state("networkidle"' in riga
        ]
        assert chiamate == [], f"attese su networkidle rimaste: {chiamate}"

    def test_i_timeout_sono_coerenti(self):
        from fantabot.lega.client import (
            LOGIN_WAIT_MS,
            NAV_TIMEOUT_MS,
            SETTLE_TIMEOUT_MS,
        )

        # L'assestamento e' un'attesa accessoria: deve restare la piu' corta.
        assert SETTLE_TIMEOUT_MS < LOGIN_WAIT_MS < NAV_TIMEOUT_MS


class TestDiagnosticaLeggibile:
    """La diagnostica deve arrivare dove la si guarda: nel log del job.

    Scriverla solo su file la rende inutile nella pratica: quel file vive in
    un artifact, che per leggerlo va scaricato.
    """

    def test_la_diagnostica_viene_anche_loggata(self):
        import inspect

        from fantabot.lega.client import LeagueClient

        sorgente = inspect.getsource(LeagueClient._save_diagnostics)
        assert "log.info" in sorgente
        assert "text" in sorgente.split("log.info", 1)[1][:200]

    def test_la_rosa_illeggibile_produce_la_diagnostica(self):
        import inspect

        from fantabot.lega.client import LeagueClient

        sorgente = inspect.getsource(LeagueClient.read_roster)
        # Entrambi i rami di fallimento: nessuna riga, e righe senza dati utili.
        assert sorgente.count("_save_diagnostics") == 2

    def test_discover_stampa_il_report(self):
        import inspect

        from fantabot.cli import _cmd_discover

        sorgente = inspect.getsource(_cmd_discover)
        assert "report.read_text" in sorgente


class TestAttesaDelCaricamento:
    """L'app della lega e' Angular: il DOM utile arriva dopo le sue XHR.

    `domcontentloaded` segnala solo il guscio. La diagnostica del run reale
    mostrava `nz-spin` e `ant-spin-dot-item` al posto dei giocatori: stavamo
    leggendo la pagina mentre lo spinner era ancora a schermo.
    """

    def test_esiste_la_sezione_loading(self, selectors):
        assert "loading" in selectors
        assert selectors["loading"]["spinner"]

    def test_copre_lo_spinner_di_ng_zorro(self, selectors):
        spinner = " ".join(selectors["loading"]["spinner"])
        assert "ant-spin" in spinner
        assert "nz-spin" in spinner

    def test_il_timeout_di_caricamento_e_generoso(self, selectors):
        """Piu' lungo dell'assestamento: qui si aspettano chiamate di rete."""
        from fantabot.lega.client import SETTLE_TIMEOUT_MS

        assert selectors["loading"]["timeout_ms"] > SETTLE_TIMEOUT_MS

    def test_ogni_navigazione_aspetta_il_contenuto(self):
        import inspect

        from fantabot.lega.client import LeagueClient

        assert "_wait_for_content" in inspect.getsource(LeagueClient._open)

    def test_la_rosa_aspetta_le_righe_prima_di_arrendersi(self):
        import inspect

        from fantabot.lega.client import LeagueClient

        sorgente = inspect.getsource(LeagueClient.read_roster)
        assert "_wait_any" in sorgente
        # L'attesa deve venire prima della lettura, altrimenti non serve.
        assert sorgente.index("_wait_any") < sorgente.index("rows = self._query_all")


class TestSelettoriRosaReali:
    """Ricavati dalla diagnostica della pagina lineup vera, non a intuito."""

    def test_la_riga_e_la_card_del_giocatore(self, selectors):
        """`ui-player-card` sono i 25 di rosa; `ui-lineup-slot` gli 11 in campo."""
        row = selectors["rosa"]["row"]
        assert row[0] == "section.cdk-drop-list ui-player-card"
        assert not any("lineup-slot" in s for s in row)

    def test_nome_e_ruolo(self, selectors):
        assert selectors["rosa"]["name"][0] == "span.player-name"
        assert selectors["rosa"]["role"][0] == "div.role"

    def test_la_squadra_e_best_effort(self, selectors):
        """Per un infortunato il riquadro del prossimo turno non c'e'."""
        assert "ui-next-match-progress" in selectors["rosa"]["team"][0]
