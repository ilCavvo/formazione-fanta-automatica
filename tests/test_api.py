"""Invio della formazione tramite l'API della lega.

Gli endpoint e la forma del payload vengono dal registro delle chiamate reali,
non da ipotesi. Qui si fissa il comportamento del client senza toccare la rete:
un finto contesto di richiesta restituisce quello che vogliamo.
"""

from __future__ import annotations

import json

import pytest

from fantabot.lega.api import (
    REQUIRED_FIELDS,
    ApiError,
    LeagueApi,
    module_to_api,
    unwrap,
)

#: Risposta osservata davvero su GET /gaming/v1/teamLineup/visualizza/A/301229
LETTURA = {
    "idcomp": 301229, "tid": 4163261, "mday": 2, "cmday": 4,
    "allComp": True, "visb": True,
    "swtcA": 0, "swtcB": 0, "swtc": 0, "swtcMdl": "",
    "mdl": "433", "capt": None,
    "starts": [6482, 4657], "bench": [6519, 4463],
}


class FintaRisposta:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status
        self.ok = 200 <= status < 300

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("non JSON")
        return self._payload


class FintoRequest:
    """Registra le chiamate e restituisce risposte preconfezionate."""

    def __init__(self, get=None, post=None):
        self._get = get
        self._post = post
        self.chiamate: list[tuple[str, str, dict | None]] = []
        self.header: list[dict] = []

    def get(self, url, headers=None, **kw):
        self.chiamate.append(("GET", url, None))
        self.header.append(dict(headers or {}))
        # Con piu' letture (rilettura di verifica) la stessa risposta va bene.
        return self._get

    def post(self, url, data=None, headers=None, **kw):
        corpo = json.loads(data) if data else None
        self.chiamate.append(("POST", url, corpo))
        self.header.append(dict(headers or {}))
        return self._post


def api(get=None, post=None, headers=None) -> tuple[LeagueApi, FintoRequest]:
    request = FintoRequest(get=get, post=post)
    return LeagueApi(request, headers=headers), request


class TestModulo:
    def test_toglie_i_trattini(self):
        assert module_to_api("4-3-3") == "433"
        assert module_to_api("5-3-2") == "532"


class TestLettura:
    def test_usa_l_endpoint_osservato(self):
        client, request = api(get=FintaRisposta(LETTURA))
        client.read_lineup(301229)
        metodo, url, _ = request.chiamate[0]
        assert metodo == "GET"
        assert url.endswith("/gaming/v1/teamLineup/visualizza/A/301229")

    def test_errore_http(self):
        client, _ = api(get=FintaRisposta({}, status=500))
        with pytest.raises(ApiError, match="500"):
            client.read_lineup(301229)

    def test_risposta_non_json(self):
        client, _ = api(get=FintaRisposta("<html>", status=200))
        with pytest.raises(ApiError, match="non JSON"):
            client.read_lineup(301229)


class TestSalvataggio:
    STARTS = list(range(101, 112))  # 11 titolari
    BENCH = [201, 202]

    def _risposta_ok(self, module="433", starts=None):
        return FintaRisposta({
            "mdl": module,
            "starts": starts if starts is not None else self.STARTS,
            "bench": self.BENCH,
            "ldate": "20260909203930174",
        })

    def test_rimanda_indietro_i_campi_che_non_capiamo(self):
        """`mday` e `cmday` valgono 2 e 4 nello stesso payload: la loro
        semantica non e' ovvia, quindi si copiano invece di ricostruirli."""
        client, request = api(post=self._risposta_ok())
        client.save_lineup(LETTURA, self.STARTS, self.BENCH, "4-3-3")
        _, url, corpo = request.chiamate[0]
        assert url.endswith("/gaming/v1/teamLineup/A")
        for campo in REQUIRED_FIELDS:
            assert corpo[campo] == LETTURA[campo]

    def test_rimanda_indietro_anche_i_campi_che_non_conosciamo(self):
        """Elencare noi i campi da tenere vorrebbe dire decidere quali contano."""
        client, request = api(post=self._risposta_ok())
        lettura = {**LETTURA, "lucnt": 8, "useId": 4129046, "pos": 0}
        client.save_lineup(lettura, self.STARTS, self.BENCH, "4-3-3")
        corpo = request.chiamate[0][2]
        assert corpo["lucnt"] == 8
        assert corpo["useId"] == 4129046

    def test_non_rimanda_il_marcatempo_del_sito(self):
        """`ldate` lo scrive il sito a ogni salvataggio."""
        client, request = api(post=self._risposta_ok())
        client.save_lineup({**LETTURA, "ldate": "20260909205644666"},
                           self.STARTS, self.BENCH, "4-3-3")
        assert "ldate" not in request.chiamate[0][2]

    def test_swtcmdl_a_null_non_blocca_l_invio(self):
        """Il sito lo manda davvero a `null`: e' un valore, non un campo che manca."""
        client, _ = api(post=self._risposta_ok())
        assert client.save_lineup({**LETTURA, "swtcMdl": None},
                                  self.STARTS, self.BENCH, "4-3-3")

    def test_sostituisce_undici_panchina_e_modulo(self):
        client, request = api(post=self._risposta_ok())
        client.save_lineup(LETTURA, self.STARTS, self.BENCH, "4-3-3")
        _, _, corpo = request.chiamate[0]
        assert corpo["starts"] == self.STARTS
        assert corpo["bench"] == self.BENCH
        assert corpo["mdl"] == "433"

    def test_capitani_null_diventa_lista_vuota(self):
        """Il sito invia `[]` e restituisce `null`: va normalizzato."""
        client, request = api(post=self._risposta_ok())
        client.save_lineup(LETTURA, self.STARTS, self.BENCH, "4-3-3")
        assert request.chiamate[0][2]["capt"] == []

    def test_esito_descritto(self):
        client, _ = api(post=self._risposta_ok())
        esito = client.save_lineup(LETTURA, self.STARTS, self.BENCH, "4-3-3")
        assert "4-3-3" in esito.describe()
        assert esito.saved_at == "20260909203930174"


class TestVerifiche:
    """La verifica sui dati e' cio' che il browser non poteva dare."""

    STARTS = list(range(101, 112))

    def test_modulo_diverso_da_quello_chiesto(self):
        client, _ = api(post=FintaRisposta({"mdl": "352", "starts": self.STARTS}))
        with pytest.raises(ApiError, match="352"):
            client.save_lineup(LETTURA, self.STARTS, [], "4-3-3")

    def test_titolari_diversi_da_quelli_inviati(self):
        alterati = self.STARTS[:-1] + [999]
        client, _ = api(post=FintaRisposta({"mdl": "433", "starts": alterati}))
        with pytest.raises(ApiError, match="non corrispondono"):
            client.save_lineup(LETTURA, self.STARTS, [], "4-3-3")

    def test_ordine_diverso_va_bene(self):
        """Conta chi c'e', non in che ordine il sito li rimanda."""
        client, _ = api(post=FintaRisposta({"mdl": "433",
                                            "starts": list(reversed(self.STARTS))}))
        assert client.save_lineup(LETTURA, self.STARTS, [], "4-3-3")


class TestControlliPrimaDiInviare:
    STARTS = list(range(101, 112))

    def test_servono_esattamente_undici_titolari(self):
        client, _ = api(post=FintaRisposta({}))
        with pytest.raises(ApiError, match="11 titolari"):
            client.save_lineup(LETTURA, self.STARTS[:10], [], "4-3-3")

    def test_niente_titolari_ripetuti(self):
        client, _ = api(post=FintaRisposta({}))
        with pytest.raises(ApiError, match="ripetuti"):
            client.save_lineup(LETTURA, [101] * 11, [], "4-3-3")

    def test_niente_stesso_giocatore_in_campo_e_in_panchina(self):
        client, _ = api(post=FintaRisposta({}))
        with pytest.raises(ApiError, match="titolari e panchina"):
            client.save_lineup(LETTURA, self.STARTS, [101], "4-3-3")

    def test_lettura_incompleta(self):
        """Meglio ripiegare sul browser che inviare un payload monco."""
        client, _ = api(post=FintaRisposta({}))
        parziale = {k: v for k, v in LETTURA.items() if k != "cmday"}
        with pytest.raises(ApiError, match="cmday"):
            client.save_lineup(parziale, self.STARTS, [], "4-3-3")


class TestHeaderDiAutenticazione:
    """Senza `authorization` e `app_key` l'API risponde 401, cookie o non cookie."""

    STARTS = list(range(101, 112))

    def test_sulla_lettura(self):
        client, request = api(get=FintaRisposta(LETTURA),
                              headers={"authorization": "Bearer x", "app_key": "k"})
        client.read_lineup(301229)
        assert request.header[0]["authorization"] == "Bearer x"
        assert request.header[0]["app_key"] == "k"

    def test_sul_salvataggio_insieme_al_content_type(self):
        risposta = FintaRisposta({"mdl": "433", "starts": self.STARTS})
        client, request = api(post=risposta, headers={"authorization": "Bearer x"})
        client.save_lineup(LETTURA, self.STARTS, [], "4-3-3")
        inviati = request.header[0]
        assert inviati["authorization"] == "Bearer x"
        assert inviati["Content-Type"] == "application/json"


class TestIncarto:
    """La lettura arriva dentro `teamLineupDto`, insieme all'elenco giocatori."""

    STARTS = list(range(101, 112))

    def test_toglie_l_incarto(self):
        client, _ = api(get=FintaRisposta({"teamLineupDto": LETTURA,
                                           "lineUpInfo": [{"pid": 1}]}))
        assert client.read_lineup(301229)["mdl"] == "433"

    def test_senza_incarto_non_cambia_niente(self):
        assert unwrap(LETTURA) is LETTURA

    def test_risposta_che_non_e_una_formazione(self):
        with pytest.raises(ApiError, match="list"):
            unwrap([1, 2, 3])

    def test_salvataggio_incartato(self):
        salvato = {"teamLineupDto": {"mdl": "433", "starts": self.STARTS}}
        client, _ = api(post=FintaRisposta(salvato))
        assert client.save_lineup(LETTURA, self.STARTS, [], "4-3-3")


class TestVerificaRileggendo:
    """Se la risposta al salvataggio non contiene la formazione, si rilegge.

    E' proprio la verifica che mancava al browser: li' si cercava un messaggio
    a schermo e il run riusciva anche salvando un modulo diverso.
    """

    STARTS = list(range(101, 112))

    def test_rilegge_quando_la_risposta_conferma_e_basta(self):
        client, request = api(
            get=FintaRisposta({"teamLineupDto": {"mdl": "433",
                                                 "starts": self.STARTS,
                                                 "ldate": "20260909203930174"}}),
            post=FintaRisposta({"esito": "ok"}),
        )
        esito = client.save_lineup(LETTURA, self.STARTS, [], "4-3-3")
        assert esito.saved_at == "20260909203930174"
        assert [m for m, _, _ in request.chiamate] == ["POST", "GET"]

    def test_la_rilettura_smaschera_un_modulo_diverso(self):
        client, _ = api(
            get=FintaRisposta({"mdl": "352", "starts": self.STARTS}),
            post=FintaRisposta({"esito": "ok"}),
        )
        with pytest.raises(ApiError, match="352"):
            client.save_lineup(LETTURA, self.STARTS, [], "4-3-3")

    def test_non_rilegge_se_la_risposta_basta(self):
        client, request = api(post=FintaRisposta({"mdl": "433",
                                                  "starts": self.STARTS}))
        client.save_lineup(LETTURA, self.STARTS, [], "4-3-3")
        assert [m for m, _, _ in request.chiamate] == ["POST"]
