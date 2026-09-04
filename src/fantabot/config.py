"""Caricamento della configurazione.

Regola: le *regole di gioco* stanno in `config/config.yaml` (versionato), i
*segreti* stanno solo nelle variabili d'ambiente (GitHub Actions Secrets).
Niente credenziali nel file di config, mai.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/config.yaml")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Secrets:
    """Segreti letti dall'ambiente. Non finiscono mai nei log."""

    username: str | None
    password: str | None
    league_slug: str | None
    team_id: str | None
    telegram_token: str | None
    telegram_chat_id: str | None

    @classmethod
    def from_env(cls) -> Secrets:
        return cls(
            username=_env("FANTACALCIO_USERNAME"),
            password=_env("FANTACALCIO_PASSWORD"),
            league_slug=_env("FANTACALCIO_LEAGUE_SLUG"),
            team_id=_env("FANTACALCIO_TEAM_ID"),
            telegram_token=_env("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


class Config:
    """Accesso a chiave puntata sul dizionario YAML, con default espliciti.

    `cfg.get("lineup.weights.probabilita", 0.0)` invece di una catena di
    `dict.get`, cosi' i moduli restano leggibili.
    """

    def __init__(self, data: dict[str, Any], path: Path | None = None) -> None:
        self._data = data
        self.path = path

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        p = Path(path) if path else DEFAULT_CONFIG_PATH
        if not p.exists():
            raise ConfigError(f"file di configurazione non trovato: {p}")
        with p.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"{p}: il contenuto non e' una mappa YAML")
        return cls(data, p)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        sentinel = object()
        value = self.get(dotted, sentinel)
        if value is sentinel:
            raise ConfigError(f"chiave di configurazione mancante: {dotted}")
        return value

    def set(self, dotted: str, value: Any) -> None:
        """Usato dall'autodetect del regolamento per sovrascrivere un default."""
        parts = dotted.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def as_dict(self) -> dict[str, Any]:
        return self._data

    # --- valori derivati che piu' moduli devono leggere allo stesso modo ----

    @property
    def dry_run(self) -> bool:
        """`DRY_RUN` nell'ambiente vince sempre sul file di config."""
        override = _env("DRY_RUN")
        if override is not None:
            return override.lower() in {"1", "true", "yes", "y", "on", "si"}
        return bool(self.get("run.dry_run", True))

    @property
    def output_dir(self) -> Path:
        return Path(str(self.get("run.output_dir", "out")))

    def league_slug(self, secrets: Secrets) -> str | None:
        return secrets.league_slug or (self.get("league.slug") or None)

    def team_id(self, secrets: Secrets) -> str | None:
        """Id della squadra dell'utente. L'ambiente vince sul file di config."""
        value = secrets.team_id or self.get("league.team_id")
        return str(value) if value else None
