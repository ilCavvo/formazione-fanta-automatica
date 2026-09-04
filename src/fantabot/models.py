"""Modelli dati condivisi da tutti i moduli.

Sono `dataclass` semplici invece di modelli pydantic perche' vengono creati e
confrontati molto spesso nei test della logica di formazione, dove la
validazione runtime sarebbe solo rumore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    """Ruolo Classic. L'ordine dell'enum e' quello di schieramento."""

    P = "P"
    D = "D"
    C = "C"
    A = "A"

    @classmethod
    def parse(cls, raw: str) -> Role:
        key = (raw or "").strip().upper()[:1]
        mapping = {"P": cls.P, "D": cls.D, "C": cls.C, "A": cls.A}
        if key not in mapping:
            raise ValueError(f"ruolo non riconosciuto: {raw!r}")
        return mapping[key]


class Status(StrEnum):
    """Verdetto su un giocatore, sia per singola fonte sia dopo aggregazione."""

    STARTER = "titolare"
    DOUBT = "dubbio"
    BENCH = "panchina"
    UNKNOWN = "sconosciuto"
    OUT = "indisponibile"


#: Peso del voto di una fonte, usato dalla media pesata in `aggregate`.
STATUS_VOTE = {
    Status.STARTER: 1.0,
    Status.DOUBT: 0.5,
    Status.BENCH: 0.0,
    Status.UNKNOWN: 0.0,
    Status.OUT: 0.0,
}


@dataclass(frozen=True)
class SourceEntry:
    """Verdetto di UNA fonte su UN giocatore."""

    source: str
    player_name: str
    team: str
    status: Status
    role: Role | None = None
    #: Percentuale di titolarita' 0-100, se la fonte la espone (fantacalcio.it).
    probability: float | None = None
    #: Modulo previsto per la squadra, se la fonte lo espone.
    team_formation: str | None = None


@dataclass
class SourceReport:
    """Tutto quello che una fonte ha detto su una giornata."""

    source: str
    ok: bool
    entries: list[SourceEntry] = field(default_factory=list)
    teams: set[str] = field(default_factory=set)
    matchweek: int | None = None
    fetched_at: datetime | None = None
    error: str | None = None

    def __len__(self) -> int:
        return len(self.entries)


@dataclass(frozen=True)
class RosterPlayer:
    """Un giocatore della MIA rosa, letto da leghe.fantacalcio.it."""

    name: str
    team: str
    role: Role
    #: Posizione nell'elenco rosa: usata come tiebreaker deterministico.
    order: int = 0
    #: Id interno della lega, quando disponibile: serve per il submit.
    player_id: str | None = None
    fantamedia: float | None = None
    #: Presenze recenti, se la lega le espone. Tiebreaker "titolarita_recente".
    recent_appearances: int | None = None


@dataclass
class PlayerVerdict:
    """Esito dell'aggregazione multi-fonte per un giocatore della mia rosa."""

    player: RosterPlayer
    status: Status
    #: Media pesata dei voti delle fonti, 0-1.
    vote: float
    #: Quante fonti (pesate) hanno espresso un parere su questo giocatore.
    voting_weight: float
    #: Quota di accordo fra le fonti che si sono espresse, 0-1.
    consensus: float
    probability: float | None = None
    #: Verdetto per fonte, per il messaggio Telegram e il debug.
    per_source: dict[str, Status] = field(default_factory=dict)
    #: Nota leggibile: perche' e' finito in questo stato.
    note: str = ""
    #: True se la squadra non risulta in campo in questa giornata.
    team_playing: bool = True
    #: Motivo dell'indisponibilita' (infortunio/squalifica), se OUT.
    out_reason: str | None = None

    @property
    def is_startable(self) -> bool:
        return self.status is not Status.OUT


@dataclass
class ScoredPlayer:
    """Giocatore con il punteggio finale usato per costruire la formazione."""

    verdict: PlayerVerdict
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def player(self) -> RosterPlayer:
        return self.verdict.player

    @property
    def role(self) -> Role:
        return self.verdict.player.role


@dataclass
class Lineup:
    """Formazione finale: titolari + panchina ordinata."""

    module: str
    starters: list[ScoredPlayer]
    bench: list[ScoredPlayer]
    module_score: float
    #: Punteggi di tutti i moduli valutati, per capire perche' ha scelto questo.
    module_scores: dict[str, float] = field(default_factory=dict)
    #: Righe di spiegazione destinate al messaggio Telegram.
    decisions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def by_role(self, role: Role) -> list[ScoredPlayer]:
        return [p for p in self.starters if p.role is role]


@dataclass
class Match:
    """Una partita di Serie A della giornata."""

    matchweek: int
    home: str
    away: str
    kickoff: datetime | None


@dataclass
class Matchday:
    """La giornata corrente con la sua deadline di schieramento."""

    matchweek: int
    matches: list[Match]
    deadline: datetime | None
    #: Da dove viene la deadline: "calendario", "lega", "fallback".
    deadline_source: str = "calendario"

    @property
    def teams(self) -> set[str]:
        out: set[str] = set()
        for m in self.matches:
            out.add(m.home)
            out.add(m.away)
        return out


@dataclass
class RunResult:
    """Riepilogo di un run completo, usato per la notifica Telegram."""

    ok: bool
    dry_run: bool
    matchday: Matchday | None = None
    lineup: Lineup | None = None
    sources: list[SourceReport] = field(default_factory=list)
    submitted: bool = False
    submit_detail: str = ""
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
