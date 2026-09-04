"""Interfaccia comune degli adattatori di fonte.

Ogni fonte deve poter fallire senza far cadere il run: `fetch()` cattura le
eccezioni e ritorna un `SourceReport` con `ok=False` e il messaggio d'errore,
cosi' l'aggregazione puo' ripiegare sulle fonti rimaste (fallback richiesto
dal brief).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fantabot.http import PoliteClient
from fantabot.models import SourceReport

log = logging.getLogger(__name__)


@dataclass
class SourceContext:
    """Servizi che il runner passa a ogni fonte."""

    client: PoliteClient
    config: object  # fantabot.config.Config
    raw_dir: Path | None = None

    def save_raw(self, name: str, text: str) -> None:
        """Salva l'HTML grezzo come artifact di debug, se abilitato."""
        if self.raw_dir is None:
            return
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        (self.raw_dir / name).write_text(text, encoding="utf-8")


class ProbableSource:
    """Classe base di un adattatore di fonte."""

    #: Chiave in `config.yaml` sotto `sources:`.
    key: str = "base"
    #: Nome leggibile, usato nei messaggi Telegram.
    label: str = "base"

    def __init__(self, ctx: SourceContext) -> None:
        self.ctx = ctx
        self.cfg = ctx.config

    # -- da implementare negli adattatori concreti --------------------------

    def _fetch(self) -> SourceReport:
        raise NotImplementedError

    # -- API pubblica -------------------------------------------------------

    def fetch(self) -> SourceReport:
        started = datetime.now(UTC)
        try:
            report = self._fetch()
        except Exception as exc:  # noqa: BLE001 - una fonte rotta non blocca il run
            log.warning("fonte %s non disponibile: %s", self.label, exc)
            return SourceReport(source=self.label, ok=False, error=str(exc),
                                fetched_at=started)
        report.fetched_at = started
        if report.ok:
            log.info("fonte %s: %d giocatori su %d squadre",
                     self.label, len(report.entries), len(report.teams))
        return report

    # -- utilita' condivise -------------------------------------------------

    def option(self, name: str, default=None):
        """Legge `sources.<key>.<name>` dalla config."""
        return self.cfg.get(f"sources.{self.key}.{name}", default)

    @property
    def enabled(self) -> bool:
        return bool(self.option("enabled", True))

    @property
    def weight(self) -> float:
        return float(self.option("weight", 1.0))
