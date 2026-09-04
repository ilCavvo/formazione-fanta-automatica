"""Utilita' condivise dai test."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fantabot.config import Config

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fixture_html():
    def _read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return _read


@pytest.fixture
def real_config() -> Config:
    """La config vera del repo: i test la usano come banco di prova."""
    path = PROJECT_ROOT / "config" / "config.yaml"
    return Config(yaml.safe_load(path.read_text(encoding="utf-8")), path)


@pytest.fixture
def config_factory(real_config):
    """Copia della config reale con alcune chiavi sovrascritte.

    Uso: `config_factory({"league.modifiers.modificatore_difesa": True})`
    """
    import copy

    def _make(overrides: dict | None = None) -> Config:
        cfg = Config(copy.deepcopy(real_config.as_dict()), real_config.path)
        for dotted, value in (overrides or {}).items():
            cfg.set(dotted, value)
        return cfg

    return _make
