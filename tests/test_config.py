"""Config, segreti e parsing del regolamento della lega."""

from __future__ import annotations

import pytest

from fantabot.config import Config, ConfigError, Secrets
from fantabot.lega.client import parse_rules_text
from fantabot.logging_setup import RedactingFilter


class TestConfig:
    def test_accesso_puntato(self, real_config):
        assert real_config.get("league.mode") == "classic"
        assert real_config.get("lineup.weights.probabilita") == 25.0

    def test_default_su_chiave_mancante(self, real_config):
        assert real_config.get("non.esiste", "fallback") == "fallback"

    def test_require_alza_su_chiave_mancante(self, real_config):
        with pytest.raises(ConfigError):
            real_config.require("non.esiste")

    def test_set_crea_i_livelli_intermedi(self):
        cfg = Config({})
        cfg.set("a.b.c", 42)
        assert cfg.get("a.b.c") == 42

    def test_file_mancante(self):
        with pytest.raises(ConfigError):
            Config.load("/percorso/che/non/esiste.yaml")

    def test_dry_run_default_attivo(self, real_config, monkeypatch):
        """Il default deve essere prudente: nessun invio reale."""
        monkeypatch.delenv("DRY_RUN", raising=False)
        assert real_config.dry_run is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no"])
    def test_env_var_disattiva_il_dry_run(self, real_config, monkeypatch, value):
        monkeypatch.setenv("DRY_RUN", value)
        assert real_config.dry_run is False

    @pytest.mark.parametrize("value", ["true", "1", "si", "yes"])
    def test_env_var_attiva_il_dry_run(self, config_factory, monkeypatch, value):
        cfg = config_factory({"run.dry_run": False})
        monkeypatch.setenv("DRY_RUN", value)
        assert cfg.dry_run is True

    def test_moduli_ammessi_sono_validi(self, real_config):
        """Ogni modulo in config deve schierare esattamente 11 titolari."""
        from fantabot.lineup import Module

        for name in real_config.get("league.allowed_modules"):
            module = Module.parse(name)
            assert 1 + module.difensori + module.centrocampisti + module.attaccanti == 11


class TestSecrets:
    def test_lettura_dallambiente(self, monkeypatch):
        monkeypatch.setenv("FANTACALCIO_USERNAME", "mario")
        monkeypatch.setenv("FANTACALCIO_PASSWORD", "segreta")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
        secrets = Secrets.from_env()
        assert secrets.has_credentials
        assert secrets.has_telegram

    def test_valori_vuoti_contano_come_assenti(self, monkeypatch):
        monkeypatch.setenv("FANTACALCIO_USERNAME", "   ")
        monkeypatch.delenv("FANTACALCIO_PASSWORD", raising=False)
        assert Secrets.from_env().has_credentials is False

    def test_slug_da_env_vince_su_config(self, real_config, monkeypatch):
        monkeypatch.setenv("FANTACALCIO_LEAGUE_SLUG", "la-mia-lega")
        assert real_config.league_slug(Secrets.from_env()) == "la-mia-lega"

    def test_nessuna_credenziale_nel_file_versionato(self, real_config):
        """Guardia contro il commit accidentale di un segreto in config.yaml."""
        blob = str(real_config.as_dict()).lower()
        for parola in ("password", "token", "chat_id"):
            assert parola not in blob


class TestRedazioneLog:
    def test_i_segreti_spariscono_dai_log(self, monkeypatch):
        monkeypatch.setenv("FANTACALCIO_PASSWORD", "SuperSegreta123")
        import logging

        record = logging.LogRecord("t", logging.INFO, "f", 1,
                                   "login con SuperSegreta123", None, None)
        RedactingFilter().filter(record)
        assert "SuperSegreta123" not in record.msg
        assert "***" in record.msg

    def test_gli_argomenti_numerici_restano_numeri(self, monkeypatch):
        """Non deve rompere i formattatori tipo %.1f."""
        monkeypatch.setenv("FANTACALCIO_PASSWORD", "SuperSegreta123")
        import logging

        record = logging.LogRecord("t", logging.INFO, "f", 1,
                                   "attesa %.1fs", (2.5,), None)
        RedactingFilter().filter(record)
        assert record.getMessage() == "attesa 2.5s"


class TestRegolamento:
    def test_deduce_classic_moduli_e_modificatore(self):
        rules = parse_rules_text(
            "Lega Classic. Moduli ammessi: 3-4-3, 3-5-2, 4-4-2, 5-3-2. "
            "Il modificatore di difesa e' attivo."
        )
        assert rules.mode == "classic"
        assert rules.allowed_modules == ["3-4-3", "3-5-2", "4-4-2", "5-3-2"]
        assert rules.modificatore_difesa is True

    def test_riconosce_mantra(self):
        assert parse_rules_text("Lega in modalita' Mantra").mode == "mantra"

    def test_modificatore_disattivato(self):
        rules = parse_rules_text("Modificatore di difesa: non attivo")
        assert rules.modificatore_difesa is False

    def test_modificatore_non_menzionato_resta_none(self):
        rules = parse_rules_text("Regolamento generico senza dettagli")
        assert rules.modificatore_difesa is None
        assert rules.modificatore_portiere is None

    def test_applied_to_sovrascrive_la_config(self, config_factory):
        cfg = config_factory({"league.modifiers.modificatore_difesa": False})
        rules = parse_rules_text("Lega Classic. Il modificatore di difesa e' attivo.")
        changes = rules.applied_to(cfg)
        assert cfg.get("league.modifiers.modificatore_difesa") is True
        assert any("modificatore difesa" in c for c in changes)

    def test_regolamento_illeggibile_non_tocca_la_config(self, config_factory):
        cfg = config_factory({"league.modifiers.modificatore_difesa": False})
        moduli_prima = cfg.get("league.allowed_modules")
        parse_rules_text("").applied_to(cfg)
        assert cfg.get("league.modifiers.modificatore_difesa") is False
        assert cfg.get("league.allowed_modules") == moduli_prima
