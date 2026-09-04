"""Aggregazione multi-fonte delle probabili formazioni.

Per ogni giocatore della MIA rosa raccogliamo il verdetto di ogni fonte
disponibile e calcoliamo una media pesata dei voti
(titolare = 1.0, dubbio = 0.5, panchina/assente = 0.0).

- media >= `starter_threshold`  -> TITOLARE
- media >= `doubt_threshold`    -> DUBBIO
- altrimenti                    -> PANCHINA

Regole che il brief chiede esplicitamente:
- naming diverso fra fonti  -> risolto da `fantabot.names.PlayerMatcher`
- fonte mancante            -> non vota, la media si calcola sulle rimanenti
- nessun consenso (1-1-1)   -> DUBBIO, poi si applicano i `tiebreakers`
- infortunati/squalificati  -> OUT, non schierabili
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fantabot.models import (
    STATUS_VOTE,
    PlayerVerdict,
    RosterPlayer,
    SourceEntry,
    SourceReport,
    Status,
)
from fantabot.names import AliasMap, PlayerMatcher
from fantabot.sources.unavailability import Unavailable

log = logging.getLogger(__name__)


@dataclass
class AggregationSettings:
    starter_threshold: float = 0.60
    doubt_threshold: float = 0.34
    min_score: int = 86
    require_same_team: bool = True
    tiebreakers: tuple[str, ...] = (
        "probabilita_media",
        "titolarita_recente",
        "fantamedia",
        "ordine_rosa",
    )

    @classmethod
    def from_config(cls, cfg) -> AggregationSettings:
        return cls(
            starter_threshold=float(cfg.get("aggregation.starter_threshold", 0.60)),
            doubt_threshold=float(cfg.get("aggregation.doubt_threshold", 0.34)),
            min_score=int(cfg.get("aggregation.matching.min_score", 86)),
            require_same_team=bool(cfg.get("aggregation.matching.require_same_team", True)),
            tiebreakers=tuple(
                cfg.get("aggregation.tiebreakers", list(cls.tiebreakers)) or ()
            ),
        )


class Aggregator:
    def __init__(
        self,
        settings: AggregationSettings,
        aliases: AliasMap | None = None,
        source_weights: dict[str, float] | None = None,
    ) -> None:
        self.settings = settings
        self.aliases = aliases or AliasMap()
        self.source_weights = source_weights or {}

    def aggregate(
        self,
        roster: list[RosterPlayer],
        reports: list[SourceReport],
        unavailable: list[Unavailable] | None = None,
        teams_playing: set[str] | None = None,
    ) -> list[PlayerVerdict]:
        matcher = PlayerMatcher(
            known=[(p.name, p.team) for p in roster],
            aliases=self.aliases,
            min_score=self.settings.min_score,
            require_same_team=self.settings.require_same_team,
        )
        by_key = {(p.name, p.team): p for p in roster}

        # (nome, squadra) canonici -> fonte -> verdetto
        votes: dict[tuple[str, str], dict[str, SourceEntry]] = {}
        healthy_sources = [r for r in reports if r.ok]
        for report in healthy_sources:
            for entry in report.entries:
                key = matcher.match(entry.player_name, entry.team)
                if key is None:
                    continue  # giocatore non mio: ignoralo
                bucket = votes.setdefault(key, {})
                # Se una fonte nomina lo stesso giocatore piu' volte (titolari +
                # riserve), tieni il verdetto piu' favorevole.
                current = bucket.get(report.source)
                if current is None or STATUS_VOTE[entry.status] > STATUS_VOTE[current.status]:
                    bucket[report.source] = entry

        out_index = self._index_unavailable(unavailable or [], matcher)

        verdicts: list[PlayerVerdict] = []
        for key, player in by_key.items():
            entries = votes.get(key, {})
            verdict = self._verdict_for(player, entries, healthy_sources)

            if key in out_index:
                reason = out_index[key]
                verdict.status = Status.OUT
                verdict.out_reason = reason
                verdict.note = f"escluso: {reason}"

            if teams_playing is not None:
                team_norm = self.aliases.canonical_team(player.team)
                playing = {self.aliases.canonical_team(t) for t in teams_playing}
                verdict.team_playing = team_norm in playing
                if not verdict.team_playing and verdict.status is not Status.OUT:
                    verdict.note = "squadra non in campo in questa giornata"

            verdicts.append(verdict)

        return verdicts

    # -- interno ------------------------------------------------------------

    def _verdict_for(
        self,
        player: RosterPlayer,
        entries: dict[str, SourceEntry],
        healthy_sources: list[SourceReport],
    ) -> PlayerVerdict:
        if not entries:
            return PlayerVerdict(
                player=player,
                status=Status.UNKNOWN,
                vote=0.0,
                voting_weight=0.0,
                consensus=0.0,
                note="nessuna fonte lo nomina",
            )

        # Una fonte che copre la squadra ma non nomina il giocatore sta di fatto
        # dicendo "panchina": vota 0 con il suo peso.
        team_covered = {
            report.source
            for report in healthy_sources
            if _covers_team(report, player.team, self.aliases)
        }

        weighted_sum = 0.0
        total_weight = 0.0
        per_source: dict[str, Status] = {}
        probabilities: list[float] = []

        for source in team_covered | set(entries):
            weight = self.source_weights.get(source, 1.0)
            entry = entries.get(source)
            status = entry.status if entry is not None else Status.BENCH
            per_source[source] = status
            weighted_sum += STATUS_VOTE[status] * weight
            total_weight += weight
            if entry is not None and entry.probability is not None:
                probabilities.append(entry.probability)

        vote = weighted_sum / total_weight if total_weight else 0.0

        # Alcune fonti marcano esplicitamente squalifica/infortunio.
        if any(status is Status.OUT for status in per_source.values()):
            declaring = [s for s, st in per_source.items() if st is Status.OUT]
            return PlayerVerdict(
                player=player,
                status=Status.OUT,
                vote=0.0,
                voting_weight=total_weight,
                consensus=1.0,
                per_source=per_source,
                note=f"indisponibile secondo {', '.join(sorted(declaring))}",
            )

        if vote >= self.settings.starter_threshold:
            status = Status.STARTER
        elif vote >= self.settings.doubt_threshold:
            status = Status.DOUBT
        else:
            status = Status.BENCH

        consensus = _consensus(per_source, status)
        probability = sum(probabilities) / len(probabilities) if probabilities else None

        note = _explain(per_source, vote, status, consensus)
        return PlayerVerdict(
            player=player,
            status=status,
            vote=vote,
            voting_weight=total_weight,
            consensus=consensus,
            probability=probability,
            per_source=per_source,
            note=note,
        )

    def _index_unavailable(
        self, unavailable: list[Unavailable], matcher: PlayerMatcher
    ) -> dict[tuple[str, str], str]:
        index: dict[tuple[str, str], str] = {}
        for item in unavailable:
            key = matcher.match(item.name, item.team)
            if key is None:
                continue
            index[key] = item.kind
        return index


def _covers_team(report: SourceReport, team: str, aliases: AliasMap) -> bool:
    target = aliases.canonical_team(team)
    return any(aliases.canonical_team(t) == target for t in report.teams)


def _consensus(per_source: dict[str, Status], final: Status) -> float:
    """Quota di fonti che concordano con il verdetto finale."""
    if not per_source:
        return 0.0
    agreeing = sum(1 for status in per_source.values() if status is final)
    return agreeing / len(per_source)


def _explain(per_source: dict[str, Status], vote: float, status: Status,
             consensus: float) -> str:
    detail = ", ".join(f"{src}={st.value}" for src, st in sorted(per_source.items()))
    if consensus >= 1.0:
        return f"tutte le fonti concordi ({detail})"
    if status is Status.DOUBT:
        return f"nessun consenso chiaro ({detail}, media {vote:.2f})"
    return f"maggioranza {status.value} ({detail}, media {vote:.2f})"


def resolve_doubt(verdict: PlayerVerdict, tiebreakers: tuple[str, ...]) -> float:
    """Valore di spareggio per due giocatori con lo stesso stato aggregato.

    Applica i criteri nell'ordine configurato e ritorna il primo disponibile,
    normalizzato in modo che "piu' alto = meglio".
    """
    player = verdict.player
    for criterion in tiebreakers:
        if criterion == "probabilita_media" and verdict.probability is not None:
            return verdict.probability
        if criterion == "titolarita_recente" and player.recent_appearances is not None:
            return float(player.recent_appearances) * 10.0
        if criterion == "fantamedia" and player.fantamedia is not None:
            return float(player.fantamedia)
        if criterion == "ordine_rosa":
            # Ordine crescente in rosa = priorita' maggiore.
            return -float(player.order)
    return 0.0
