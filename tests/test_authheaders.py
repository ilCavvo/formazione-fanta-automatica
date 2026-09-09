"""Header di autenticazione copiati dalle richieste dell'app.

Il registro delle chiamate reali mostra che ogni richiesta verso
`apileague.fantacalcio.it` porta `app_key` e `authorization`: qui si fissa che
vengano copiati quelli e non gli header legati alla singola richiesta.
"""

from __future__ import annotations

from fantabot.lega.authheaders import API_HOST, AuthHeaders

#: Nomi osservati davvero su GET /gaming/v1/teamLineup/visualizza/A/301229.
OSSERVATI = {
    ":authority": "apileague.fantacalcio.it",
    ":method": "GET",
    ":path": "/gaming/v1/teamLineup/visualizza/A/301229",
    ":scheme": "https",
    "app_key": "chiave-dell-app",
    "authorization": "Bearer token-di-sessione",
    "cachable": "true",
    "priority": "u=1, i",
}

URL_API = f"https://{API_HOST}/gaming/v1/teamLineup/visualizza/A/301229"


class TestQualiRichiesteGuardare:
    def test_solo_l_host_dell_api(self):
        auth = AuthHeaders()
        assert auth.wants(URL_API)
        assert not auth.wants("https://leghe.fantacalcio.it/fantasantos/view")
        assert not auth.wants("https://www.fantacalcio.it/api/v1/User/login")

    def test_una_richiesta_di_un_altro_host_non_conta(self):
        auth = AuthHeaders()
        assert not auth.observe("https://leghe.fantacalcio.it/x", OSSERVATI)
        assert not auth.ready


class TestCosaSiCopia:
    def test_copia_autenticazione_e_chiave_dell_app(self):
        auth = AuthHeaders()
        assert auth.observe(URL_API, OSSERVATI)
        copiati = auth.as_dict()
        assert copiati["authorization"] == "Bearer token-di-sessione"
        assert copiati["app_key"] == "chiave-dell-app"

    def test_scarta_gli_pseudo_header(self):
        auth = AuthHeaders()
        auth.observe(URL_API, OSSERVATI)
        assert not [h for h in auth.as_dict() if h.startswith(":")]

    def test_scarta_quelli_della_singola_richiesta(self):
        """`content-length` di un altro corpo e' peggio che inutile."""
        auth = AuthHeaders()
        auth.observe(URL_API, {**OSSERVATI, "content-length": "123",
                               "content-type": "application/json",
                               "cookie": "sid=1", "sec-fetch-mode": "cors"})
        copiati = auth.as_dict()
        for indesiderato in ("cachable", "priority", "content-length",
                             "content-type", "cookie", "sec-fetch-mode"):
            assert indesiderato not in copiati

    def test_copia_anche_header_che_non_conosciamo(self):
        """Se domani il sito ne aggiunge uno, deve arrivare da solo."""
        auth = AuthHeaders()
        auth.observe(URL_API, {**OSSERVATI, "x-device-id": "abc"})
        assert auth.as_dict()["x-device-id"] == "abc"

    def test_i_nomi_arrivano_normalizzati(self):
        auth = AuthHeaders()
        auth.observe(URL_API, {"Authorization": "Bearer x", "App_Key": "k"})
        assert sorted(auth.as_dict()) == ["app_key", "authorization"]


class TestQuandoServonoDavvero:
    def test_senza_authorization_non_valgono(self):
        """Le chiamate pubbliche non portano il token: copiarle non aiuta."""
        auth = AuthHeaders()
        senza = {k: v for k, v in OSSERVATI.items() if k != "authorization"}
        assert not auth.observe(URL_API, senza)
        assert not auth.ready

    def test_una_chiamata_pubblica_non_cancella_quelli_buoni(self):
        auth = AuthHeaders()
        auth.observe(URL_API, OSSERVATI)
        auth.observe(URL_API, {"app_key": "chiave-dell-app"})
        assert auth.as_dict()["authorization"] == "Bearer token-di-sessione"

    def test_l_ultimo_token_vince(self):
        """Se la sessione si rinnova a meta' run, vale il token nuovo."""
        auth = AuthHeaders()
        auth.observe(URL_API, OSSERVATI)
        auth.observe(URL_API, {**OSSERVATI, "authorization": "Bearer nuovo"})
        assert auth.as_dict()["authorization"] == "Bearer nuovo"


class TestNienteSegretiNeiLog:
    def test_descrive_solo_i_nomi(self):
        auth = AuthHeaders()
        auth.observe(URL_API, OSSERVATI)
        descrizione = auth.describe()
        assert descrizione == "app_key, authorization"
        assert "token-di-sessione" not in descrizione
        assert "chiave-dell-app" not in descrizione

    def test_senza_niente_lo_dice(self):
        assert AuthHeaders().describe() == "nessuno"
