"""Invio della formazione tramite l'API della lega.

Gli endpoint sono stati ricavati registrando le chiamate dell'app (vedi
`apitrace.py`), non indovinati:

    GET  /gaming/v1/teamLineup/visualizza/A/{idcomp}   legge la formazione
    POST /gaming/v1/teamLineup/A                       la salva

La strategia e' **leggi, modifica, riscrivi**: si parte dall'oggetto che il
sito stesso restituisce e si sostituiscono solo titolari, panchina e modulo.
Cosi' campi come `mday` e `cmday`, la cui semantica non e' ovvia (nello stesso
payload valgono 2 e 4), vengono rimandati indietro come sono invece di essere
ricostruiti a intuito — sbagliarli significherebbe schierare per la giornata
sbagliata.

Cookie non bastano: l'app manda anche `authorization` e `app_key`, e senza
quelli l'API risponde `401`. Gli header arrivano da `authheaders.py`, che li
copia da una richiesta vera del browser.

Il vantaggio rispetto all'automazione del DOM non e' solo la velocita': qui il
modulo e gli undici si scrivono in modo esplicito e si **rileggono dalla
risposta** per verificarli. Cliccando sui nomi invece l'app ricomponeva una
formazione sua, e il run riusciva salvando altro.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

BASE_URL = "https://apileague.fantacalcio.it"
READ_PATH = "/gaming/v1/teamLineup/visualizza/A/{idcomp}"
SAVE_PATH = "/gaming/v1/teamLineup/A"

#: La lettura arriva incartata insieme all'elenco dei giocatori: la formazione
#: vera e propria sta qui dentro.
ENVELOPE_FIELD = "teamLineupDto"

#: Lo mette il sito a ogni salvataggio: rimandarglielo indietro non ha senso.
SERVER_FIELDS = ("ldate",)

#: Campi che devono esserci nella lettura perche' valga la pena inviare: se
#: mancano abbiamo letto qualcosa che non e' una formazione, e conviene
#: ripiegare sul browser invece di mandare un payload monco.
REQUIRED_FIELDS = (
    "idcomp", "tid", "mday", "cmday",
    "allComp", "visb", "swtcA", "swtcB", "swtc", "swtcMdl",
)


class ApiError(RuntimeError):
    """L'API non ha risposto come previsto: si ripiega sul browser."""


@dataclass
class SaveOutcome:
    module: str
    starters: list[int]
    bench: list[int]
    #: Marcatempo dell'ultimo salvataggio restituito dal sito.
    saved_at: str | None = None

    def describe(self) -> str:
        quando = f", ultimo salvataggio {self.saved_at}" if self.saved_at else ""
        return (f"formazione {self.module} inviata via API e verificata nella "
                f"risposta ({len(self.starters)} titolari{quando})")


def module_to_api(module: str) -> str:
    """`4-3-3` -> `433`, che e' il formato usato dall'API."""
    return module.replace("-", "")


def unwrap(data: object) -> dict:
    """Toglie l'incarto `teamLineupDto` quando c'e', altrimenti lascia stare.

    La lettura arriva incartata, ma non diamo per scontato che lo sia anche la
    risposta al salvataggio: quella non l'abbiamo mai vista.
    """
    if not isinstance(data, dict):
        raise ApiError(f"attesa una formazione, ricevuto {type(data).__name__}")
    dentro = data.get(ENVELOPE_FIELD)
    return dentro if isinstance(dentro, dict) else data


def _looks_like_lineup(data: dict) -> bool:
    return bool(data.get("mdl")) and bool(data.get("starts"))


class LeagueApi:
    """Chiamate alla lega usando la sessione gia' aperta dal browser.

    Non rifa' il login: riusa il contesto di Playwright, che porta con se' i
    cookie ottenuti dall'accesso.
    """

    def __init__(self, request, base_url: str = BASE_URL, timeout_ms: int = 20_000,
                 headers: dict[str, str] | None = None):
        self._request = request
        self.base_url = base_url.rstrip("/")
        self.timeout_ms = timeout_ms
        # `authorization` e `app_key`, copiati da una richiesta dell'app: senza
        # di loro ogni chiamata torna 401, cookie o non cookie.
        self._headers = dict(headers or {})

    def _with(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {**self._headers, **(extra or {})}

    # -- lettura ------------------------------------------------------------

    def read_lineup(self, idcomp: str | int) -> dict:
        url = self.base_url + READ_PATH.format(idcomp=idcomp)
        response = self._request.get(url, headers=self._with(),
                                     timeout=self.timeout_ms)
        if not response.ok:
            raise ApiError(f"lettura formazione fallita: {url} -> {response.status}")
        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise ApiError(f"lettura formazione: risposta non JSON da {url}") from exc
        if not isinstance(data, dict):
            raise ApiError(f"lettura formazione: risposta inattesa da {url}")
        lineup = unwrap(data)
        log.info("formazione attuale letta via API (modulo %s, giornata %s)",
                 lineup.get("mdl"), lineup.get("cmday"))
        return lineup

    # -- scrittura ----------------------------------------------------------

    def save_lineup(self, current: dict, starters: list[int], bench: list[int],
                    module: str) -> SaveOutcome:
        """Salva la formazione e **verifica** che il sito abbia accettato."""
        if len(starters) != 11:
            raise ApiError(f"servono 11 titolari, non {len(starters)}")
        if len(set(starters)) != len(starters):
            raise ApiError("ci sono titolari ripetuti")
        sovrapposti = set(starters) & set(bench)
        if sovrapposti:
            raise ApiError(f"stessi giocatori fra titolari e panchina: {sovrapposti}")

        current = unwrap(current)
        # Si guarda la presenza, non il valore: `swtcMdl` arriva davvero a
        # `null` dal sito, e trattarlo come "manca" bloccherebbe ogni invio.
        mancanti = [f for f in REQUIRED_FIELDS if f not in current]
        if mancanti:
            raise ApiError(f"la lettura non contiene i campi {mancanti}")

        # Si rimanda indietro tutto quello che il sito ci ha dato, cambiando
        # solo cio' che vogliamo cambiare: e' il senso di "leggi, modifica,
        # riscrivi". Elencare noi i campi da tenere significherebbe decidere
        # quali contano, e non lo sappiamo.
        payload = {k: v for k, v in current.items() if k not in SERVER_FIELDS}
        payload["starts"] = starters
        payload["bench"] = bench
        payload["mdl"] = module_to_api(module)
        # Il sito invia una lista vuota quando non ci sono capitani, e ce la
        # restituisce come null: normalizziamo a lista.
        payload["capt"] = current.get("capt") or []

        url = self.base_url + SAVE_PATH
        response = self._request.post(
            url,
            data=json.dumps(payload),
            headers=self._with({"Content-Type": "application/json"}),
            timeout=self.timeout_ms,
        )
        if not response.ok:
            raise ApiError(f"salvataggio fallito: {url} -> {response.status}")

        try:
            saved = unwrap(response.json())
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ApiError("salvataggio: risposta non JSON") from exc

        if not _looks_like_lineup(saved):
            # Alcune risposte confermano soltanto ("ok", un identificativo).
            # Allora la verifica si fa rileggendo: costa una GET e vale piu'
            # di un messaggio a schermo.
            log.info("la risposta al salvataggio non contiene la formazione: "
                     "la rileggo per verificarla")
            saved = self.read_lineup(current["idcomp"])

        return self._verify(saved, starters, module)

    @staticmethod
    def _verify(saved: dict, starters: list[int], module: str) -> SaveOutcome:
        """Controlla che il salvato sia davvero quello che avevamo chiesto.

        E' la verifica che con il browser mancava: li' si cercava un messaggio
        a schermo, qui si confrontano i dati.
        """
        atteso = module_to_api(module)
        ottenuto = str(saved.get("mdl") or "")
        if ottenuto != atteso:
            raise ApiError(
                f"il sito ha salvato il modulo {ottenuto!r} invece di {atteso!r}"
            )

        salvati = [int(p) for p in (saved.get("starts") or [])]
        if set(salvati) != set(starters):
            mancano = sorted(set(starters) - set(salvati))
            aggiunti = sorted(set(salvati) - set(starters))
            raise ApiError(
                "i titolari salvati non corrispondono a quelli inviati "
                f"(mancano {mancano}, in piu' {aggiunti})"
            )

        return SaveOutcome(
            module=module,
            starters=salvati,
            bench=[int(p) for p in (saved.get("bench") or [])],
            saved_at=saved.get("ldate"),
        )
