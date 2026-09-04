"""Formattazione del messaggio Telegram (nessuna rete)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fantabot.lineup import LineupSettings, build_lineup
from fantabot.models import (
    Match,
    Matchday,
    PlayerVerdict,
    Role,
    RosterPlayer,
    RunResult,
    SourceReport,
    Status,
)
from fantabot.notify import TelegramNotifier, format_result

ROME = ZoneInfo("Europe/Rome")


def verdict(name, role, status=Status.STARTER, order=0, note=""):
    v = PlayerVerdict(
        player=RosterPlayer(name=name, team="Genoa", role=role, order=order),
        status=status, vote=1.0, voting_weight=3.0, consensus=1.0,
    )
    v.note = note
    return v


def sample_lineup():
    verdicts = (
        [verdict("Bijlow", Role.P)]
        + [verdict(f"Dif{i}", Role.D, order=i + 1) for i in range(4)]
        + [verdict(f"Cen{i}", Role.C, order=i + 5) for i in range(4)]
        + [verdict("Att0", Role.A, order=9), verdict("Att1", Role.A, order=10)]
        + [verdict("Panca", Role.D, Status.BENCH, order=11)]
    )
    return build_lineup(verdicts, LineupSettings(modules=("4-4-2",)))


def sample_result(**kw) -> RunResult:
    base = dict(
        ok=True,
        dry_run=True,
        matchday=Matchday(
            matchweek=3,
            matches=[Match(3, "Genoa", "Como", datetime(2026, 9, 4, 20, 45, tzinfo=ROME))],
            deadline=datetime(2026, 9, 4, 20, 45, tzinfo=ROME),
        ),
        lineup=sample_lineup(),
        sources=[
            SourceReport(source="Fantacalcio.it", ok=True, entries=[], teams={"Genoa"}),
            SourceReport(source="Gazzetta", ok=False, error="articolo non leggibile"),
        ],
        started_at=datetime(2026, 9, 4, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 9, 4, 10, 0, 42, tzinfo=UTC),
    )
    base.update(kw)
    return RunResult(**base)


class TestFormatResult:
    def test_dry_run_dichiarato(self):
        text = format_result(sample_result())
        assert "DRY RUN" in text
        assert "non inviata" in text

    def test_submit_riuscito(self):
        text = format_result(sample_result(dry_run=False, submitted=True))
        assert "formazione inviata" in text

    def test_run_fallito(self):
        text = format_result(sample_result(ok=False, error="login fallito"))
        assert "run FALLITO" in text
        assert "login fallito" in text

    def test_mostra_modulo_e_titolari(self):
        text = format_result(sample_result())
        assert "Formazione 4-4-2" in text
        assert "Bijlow" in text
        assert "Att0" in text

    def test_mostra_stato_delle_fonti(self):
        text = format_result(sample_result())
        assert "Fantacalcio.it" in text
        assert "Gazzetta" in text
        assert "articolo non leggibile" in text

    def test_giornata_e_deadline(self):
        text = format_result(sample_result())
        assert "Giornata <b>3</b>" in text
        assert "04/09 20:45" in text

    def test_panchina_opzionale(self):
        assert "Panchina" not in format_result(sample_result(), include_bench=False)
        assert "Panchina" in format_result(sample_result(), include_bench=True)

    def test_decisioni_opzionali(self):
        text = format_result(sample_result(), include_decisions=False)
        assert "Scelte e dubbi" not in text

    def test_html_escape_sui_nomi(self):
        result = sample_result(error="<script>alert(1)</script>")
        text = format_result(result)
        assert "<script>" not in text
        assert "&lt;script&gt;" in text


class TestNotifier:
    def test_non_configurato_non_invia(self, caplog):
        notifier = TelegramNotifier(token=None, chat_id=None)
        assert notifier.configured is False
        assert notifier.send("ciao") is False

    def test_disabilitato_da_config(self):
        notifier = TelegramNotifier(token="t", chat_id="c", enabled=False)
        assert notifier.configured is False

    def test_split_messaggi_lunghi(self):
        from fantabot.notify import _split

        text = "\n".join(f"riga {i}" for i in range(2000))
        chunks = _split(text, 4000)
        assert len(chunks) > 1
        assert all(len(c) <= 4000 for c in chunks)
        assert "\n".join(chunks) == text
