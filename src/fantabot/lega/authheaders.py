"""Header di autenticazione dell'app della lega.

L'API di `apileague.fantacalcio.it` non si accontenta dei cookie: il registro
delle chiamate reali mostra che ogni richiesta dell'app porta con se' anche

    app_key, authorization

e senza quei due la stessa URL risponde `401`. I valori non sono deducibili —
`authorization` e' un token emesso da `POST /onboarding/v1/login`, `app_key`
una costante compilata dentro il bundle Angular — quindi invece di provare a
ricostruirli **li copiamo da una richiesta che l'app ha gia' fatto** mentre il
browser navigava. Cosi' non ci sono formati da indovinare e, se domani il sito
aggiunge un header, viene copiato anche quello senza toccare il codice.

I valori restano in memoria e non finiscono mai nel registro delle chiamate
ne' nei log: di loro si scrivono solo i nomi.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)

#: Host le cui richieste portano gli header che ci servono.
API_HOST = "apileague.fantacalcio.it"

#: Header legati alla singola richiesta: ricopiarli sulla nostra sarebbe
#: sbagliato (`content-length` di un altro corpo) o inutile (`:path`).
#: Tutto il resto viene ricopiato, header di autenticazione compresi.
PER_REQUEST_HEADERS = frozenset({
    "accept", "accept-encoding", "accept-language",
    "cachable", "cache-control", "connection", "content-length",
    "content-type", "pragma",
    "cookie", "host", "if-modified-since", "if-none-match",
    "origin", "priority", "referer", "user-agent",
})

#: Prefissi di header che il browser genera da se': non vanno riprodotti.
PER_REQUEST_PREFIXES = (":", "sec-", "proxy-")

#: Senza almeno questo, copiare gli header non serve a niente.
REQUIRED_HEADERS = ("authorization",)


class AuthHeaders:
    """Raccoglie gli header applicativi che l'app manda all'API della lega."""

    def __init__(self, host: str = API_HOST) -> None:
        self.host = host
        self._headers: dict[str, str] = {}

    # -- raccolta -----------------------------------------------------------

    def wants(self, url: str) -> bool:
        netloc = urlparse(url).netloc
        return netloc == self.host or netloc.endswith("." + self.host)

    def observe(self, url: str, headers: dict[str, str]) -> bool:
        """Adotta gli header di una richiesta dell'app. Ritorna se sono utili.

        Le richieste successive sovrascrivono le precedenti: se il token viene
        rinnovato durante il run, teniamo l'ultimo.
        """
        if not self.wants(url):
            return False
        utili = {
            name.lower(): value
            for name, value in (headers or {}).items()
            if _is_application_header(name.lower())
        }
        if not all(h in utili for h in REQUIRED_HEADERS):
            return False
        nuovo = not self._headers
        self._headers = utili
        if nuovo:
            log.info("header di autenticazione dell'API raccolti: %s", self.describe())
        return True

    # -- uso ----------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return bool(self._headers)

    def as_dict(self) -> dict[str, str]:
        return dict(self._headers)

    def describe(self) -> str:
        """Solo i **nomi**: i valori sono credenziali e non si scrivono."""
        return ", ".join(sorted(self._headers)) or "nessuno"


def _is_application_header(name: str) -> bool:
    if name.startswith(PER_REQUEST_PREFIXES):
        return False
    return name not in PER_REQUEST_HEADERS
