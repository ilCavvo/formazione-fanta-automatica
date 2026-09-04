"""Logging su console + file, con redazione dei segreti.

Il file di log viene pubblicato come artifact della GitHub Action, quindi deve
essere sicuro da leggere: qualunque occorrenza di password/token viene sostituita
prima della scrittura.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_SECRET_ENV_VARS = (
    "FANTACALCIO_PASSWORD",
    "FANTACALCIO_USERNAME",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)


class RedactingFilter(logging.Filter):
    """Sostituisce i valori dei secret con `***` in messaggi e argomenti."""

    def __init__(self) -> None:
        super().__init__()
        self._values = sorted(
            (v for v in (os.environ.get(k) for k in _SECRET_ENV_VARS) if v and len(v) >= 4),
            key=len,
            reverse=True,
        )

    def _scrub(self, text: str) -> str:
        for value in self._values:
            if value in text:
                text = text.replace(value, "***")
        return text

    def _scrub_arg(self, value: object) -> object:
        # Solo le stringhe vengono ripulite: convertire tutto a str romperebbe
        # i formattatori numerici (%d, %.1f) usati nei log.
        return self._scrub(value) if isinstance(value, str) else value

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._values:
            return True
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub_arg(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._scrub_arg(a) for a in record.args)
        return True


def setup_logging(level: str = "INFO", output_dir: Path | None = None,
                  filename: str = "fantabot.log") -> Path | None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    redactor = RedactingFilter()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
                            datefmt="%H:%M:%S")

    console = logging.StreamHandler()
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(fmt)
    console.addFilter(redactor)
    root.addHandler(console)

    log_path: Path | None = None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / filename
        file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="w")
        file_handler.setLevel(logging.DEBUG)  # il file tiene sempre il DEBUG
        file_handler.setFormatter(fmt)
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)

    # httpx logga ogni richiesta a INFO: troppo rumore, lo alziamo.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return log_path
