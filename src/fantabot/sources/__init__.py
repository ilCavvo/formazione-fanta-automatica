"""Adattatori verso le fonti pubbliche delle probabili formazioni."""

from fantabot.sources.base import ProbableSource, SourceContext
from fantabot.sources.fantacalcio_it import FantacalcioItSource
from fantabot.sources.gazzetta import GazzettaSource
from fantabot.sources.sky import SkySource

#: Chiave usata in `config.yaml` -> classe dell'adattatore.
REGISTRY: dict[str, type[ProbableSource]] = {
    "fantacalcio_it": FantacalcioItSource,
    "sky": SkySource,
    "gazzetta": GazzettaSource,
}

__all__ = [
    "REGISTRY",
    "ProbableSource",
    "SourceContext",
    "FantacalcioItSource",
    "SkySource",
    "GazzettaSource",
]
