"""Registro delle chiamate di rete della lega.

Il registro finisce nel log del job e negli artifact, quindi la proprieta'
piu' importante non e' la completezza ma la **sicurezza**: nessun valore
sensibile deve poterne uscire.
"""

from __future__ import annotations

import json

from fantabot.lega.apitrace import ApiTrace

API = "https://apileague.fantacalcio.it"


def trace(**kw) -> ApiTrace:
    return ApiTrace(**kw)


class TestFiltroHost:
    def test_tiene_gli_host_di_fantacalcio(self):
        t = trace()
        assert t.wants(f"{API}/v1/lineup") is True
        assert t.wants("https://leghe.fantacalcio.it/x") is True

    def test_scarta_gli_altri(self):
        t = trace()
        assert t.wants("https://www.google-analytics.com/collect") is False
        assert t.wants("https://cdn.pubtech.ai/cmp.js") is False

    def test_non_si_fa_ingannare_da_un_host_che_finisce_uguale(self):
        """`evilfantacalcio.it` non e' un sottodominio di `fantacalcio.it`."""
        assert trace().wants("https://evilfantacalcio.it/x") is False


class TestFiltroRisorse:
    def test_registra_solo_xhr_e_fetch(self):
        t = trace()
        assert t.record_request("GET", f"{API}/v1/rosa", None, "xhr") is not None
        assert t.record_request("GET", f"{API}/img/logo.png", None, "image") is None
        assert t.record_request("GET", f"{API}/app.js", None, "script") is None

    def test_senza_tipo_registra_comunque(self):
        assert trace().record_request("GET", f"{API}/v1/rosa", None) is not None


class TestRedazione:
    PAYLOAD = json.dumps({
        "password": "SuperSegreta123",
        "username": "mario",
        "formation": "5-3-2",
        "players": [{"id": 6519, "slot": 1}],
        "nota": "scritta da SuperSegreta123",
    })

    def _report(self) -> str:
        t = trace(secrets=("SuperSegreta123", "mario"))
        t.record_request("POST", f"{API}/v1/lineup/save", self.PAYLOAD, "xhr")
        return t.to_markdown()

    def test_le_chiavi_sensibili_spariscono(self):
        report = self._report()
        assert "SuperSegreta123" not in report
        assert "mario" not in report

    def test_anche_dentro_una_chiave_innocua(self):
        """Il segreto va cancellato ovunque compaia, non solo dove ce lo aspettiamo."""
        assert "SuperSegreta123" not in self._report()

    def test_ma_il_formato_utile_resta(self):
        """Senza modulo e id giocatore il registro non servirebbe a niente."""
        report = self._report()
        assert "5-3-2" in report
        assert "6519" in report
        assert "slot" in report

    def test_i_valori_delle_query_non_vengono_registrati(self):
        t = trace()
        t.record_request("GET", f"{API}/v1/rosa?sessionId=SEGRETO&teamId=42", None, "xhr")
        report = t.to_markdown()
        assert "SEGRETO" not in report
        assert "sessionId" in report  # il nome del parametro si', il valore no


class TestForma:
    def test_le_liste_lunghe_vengono_riassunte(self):
        t = trace()
        corpo = json.dumps({"players": [{"id": i} for i in range(25)]})
        t.record_request("POST", f"{API}/v1/lineup", corpo, "xhr")
        report = t.to_markdown()
        assert "25 elementi in tutto" in report

    def test_la_profondita_e_limitata(self):
        """Un annidamento infinito non deve produrre un report infinito."""
        t = trace()
        corpo = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
        t.record_request("POST", f"{API}/v1/x", json.dumps(corpo), "xhr")
        assert "..." in t.to_markdown()

    def test_un_corpo_non_json_non_esplode(self):
        t = trace()
        t.record_request("POST", f"{API}/v1/x", "non-json-affatto", "xhr")
        assert "non JSON" in t.to_markdown()


class TestRisposte:
    def test_stato_e_forma_della_risposta(self):
        t = trace()
        t.record_request("POST", f"{API}/v1/lineup/save", "{}", "xhr")
        t.record_response(f"{API}/v1/lineup/save", "POST", 200, '{"success": true}')
        report = t.to_markdown()
        assert "-> 200" in report
        assert "success" in report

    def test_una_risposta_senza_richiesta_registrata_viene_ignorata(self):
        t = trace()
        t.record_response(f"{API}/v1/mai-vista", "GET", 200, "{}")
        assert "mai-vista" not in t.to_markdown()


class TestReportVuoto:
    def test_lo_dice(self):
        assert "Nessuna chiamata registrata" in trace().to_markdown()


class TestRumoreDegliAsset:
    """Icone e immagini riempirebbero il registro senza dire nulla di utile.

    Peggio: spingono fuori dalla coda del log le chiamate che contano, ed e'
    dalla coda che il log del job si legge.
    """

    def test_scarta_i_cdn_di_asset(self):
        t = trace()
        assert t.wants("https://static.fantacalcio.it/icons/percent.svg") is False
        assert t.wants("https://content.fantacalcio.it/img/x.png") is False

    def test_ma_tiene_l_api(self):
        assert trace().wants(f"{API}/gaming/v1/teamLineup/A") is True


class TestIndice:
    def test_l_indice_sta_in_fondo(self):
        """Il log si legge dalla coda: cio' che sta in cima sparisce per primo."""
        t = trace()
        t.record_request("POST", f"{API}/gaming/v1/teamLineup/A", "{}", "xhr")
        t.record_response(f"{API}/gaming/v1/teamLineup/A", "POST", 200, "{}")
        report = t.to_markdown()
        assert "## Indice delle chiamate" in report
        indice = report.index("## Indice delle chiamate")
        assert indice > report.index("## POST")
        assert "gaming/v1/teamLineup/A` -> 200" in report[indice:]


class TestHeader:
    """Un 401 significa quasi sempre un header che il browser non mette da solo.

    Registrarne i **nomi** dice come l'app si autentica; i valori sono
    esattamente il token che non deve uscire.
    """

    def test_registra_i_nomi_non_i_valori(self):
        t = trace()
        t.record_request("GET", f"{API}/v1/x", None, "xhr",
                         {"Authorization": "Bearer SEGRETISSIMO"})
        report = t.to_markdown()
        assert "authorization" in report
        assert "SEGRETISSIMO" not in report

    def test_scarta_gli_header_che_mette_il_browser(self):
        from fantabot.lega.apitrace import _header_names

        nomi = _header_names({
            "Cookie": "c", "User-Agent": "u", "Accept": "*/*",
            "sec-ch-ua": "x", "Referer": "r",
            "X-Auth-Token": "t", "Content-Type": "application/json",
        })
        assert nomi == ["content-type", "x-auth-token"]

    def test_senza_header_la_riga_non_compare(self):
        t = trace()
        t.record_request("GET", f"{API}/v1/x", None, "xhr", {"Cookie": "c"})
        assert "header inviati" not in t.to_markdown()


class TestChiaviDiStorage:
    def test_elenca_solo_i_nomi(self):
        t = trace()
        t.record_request("GET", f"{API}/v1/x", None, "xhr")
        t.storage_keys = {"local": ["auth-token", "utente"], "session": []}
        report = t.to_markdown()
        assert "localStorage: auth-token, utente" in report
        assert "sessionStorage: (vuoto)" in report

    def test_senza_storage_la_sezione_non_compare(self):
        t = trace()
        t.record_request("GET", f"{API}/v1/x", None, "xhr")
        assert "Dove l'app tiene" not in t.to_markdown()


class TestChiaviSensibiliPerContenuto:
    """Il confronto esatto lasciava passare `token_auth`.

    La risposta di `POST /onboarding/v1/login` contiene `token_auth` e
    `state_auth`: nessuna delle due era uguale a una chiave vietata, quindi il
    token e' finito nel log del job — che e' pubblico. Ora una chiave e'
    sensibile se **contiene** una parola vietata.
    """

    def _forma(self, corpo):
        return ApiTrace().shape(corpo)

    def test_il_token_del_login_non_esce(self):
        forma = self._forma({"token_auth": "3587F44616A0531B15680D86693CA635",
                             "state_auth": 1603484913406})
        assert forma["token_auth"] == "***"
        assert forma["state_auth"] == "***"

    def test_vale_anche_annidato(self):
        forma = self._forma({"data": {"user": {"access_token": "abc"}}})
        assert forma["data"]["user"] == "***"

    def test_i_campi_normali_restano_leggibili(self):
        """Il registro serve a capire il formato: la forma deve restare utile."""
        forma = self._forma({"mdl": "433", "idcomp": 301229, "visb": True})
        assert forma == {"mdl": "433", "idcomp": 301229, "visb": True}

    def test_meglio_nascondere_troppo_che_troppo_poco(self):
        assert self._forma({"authorized": True})["authorized"] == "***"
