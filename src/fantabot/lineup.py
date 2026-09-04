"""Costruzione dinamica della formazione.

Il modulo NON e' fisso: si valutano tutti i moduli ammessi dal regolamento e si
sceglie quello col punteggio complessivo migliore. Ogni peso, bonus e malus e'
un parametro di `config.yaml`, mai un numero scritto nel codice.

Perche' questa e' una scelta ottima e non euristica: in Classic i ruoli sono
disgiunti (P/D/C/A), quindi dato un modulo il modo migliore di riempirlo e'
prendere gli N giocatori col punteggio piu' alto per ciascun reparto. Ci basta
quindi enumerare i moduli ammessi (sono 7) e confrontare i totali.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from fantabot.aggregate import resolve_doubt
from fantabot.models import Lineup, PlayerVerdict, Role, ScoredPlayer, Status

log = logging.getLogger(__name__)

_MODULE = re.compile(r"^(\d)-(\d)-(\d)$")


class LineupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Module:
    """Un modulo ammesso, es. `4-4-2`."""

    name: str
    difensori: int
    centrocampisti: int
    attaccanti: int

    @classmethod
    def parse(cls, text: str) -> Module:
        match = _MODULE.match(text.strip())
        if not match:
            raise LineupError(f"modulo non riconosciuto: {text!r}")
        d, c, a = (int(g) for g in match.groups())
        if 1 + d + c + a != 11:
            raise LineupError(f"modulo {text!r}: i titolari non sono 11")
        return cls(name=text.strip(), difensori=d, centrocampisti=c, attaccanti=a)

    def slots(self) -> dict[Role, int]:
        return {
            Role.P: 1,
            Role.D: self.difensori,
            Role.C: self.centrocampisti,
            Role.A: self.attaccanti,
        }


@dataclass
class LineupSettings:
    modules: tuple[str, ...] = ("3-4-3", "3-5-2", "4-3-3", "4-4-2", "4-5-1", "5-3-2", "5-4-1")
    bench_size: int = 14
    modificatore_difesa: bool = False

    score_starter: float = 100.0
    score_doubt: float = 45.0
    score_bench: float = 8.0
    score_unknown: float = 25.0
    score_out: float = -1000.0

    w_probabilita: float = 25.0
    w_consenso: float = 15.0
    w_fantamedia: float = 8.0
    w_ordine_rosa: float = 0.5

    pen_squadra_ferma: float = 500.0
    pen_dubbio: float = 12.0

    bonus_difesa_4: float = 60.0
    bonus_difesa_5: float = 25.0
    module_preferences: dict[str, float] = field(default_factory=dict)

    allow_incomplete: bool = True
    bench_strategy: str = "per_ruolo_poi_punteggio"
    tiebreakers: tuple[str, ...] = ("probabilita_media", "ordine_rosa")

    slot_limits: dict[str, tuple[int, int]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg) -> LineupSettings:
        slots = cfg.get("league.slots", {}) or {}

        def limits(name: str, default: tuple[int, int]) -> tuple[int, int]:
            node = slots.get(name)
            if isinstance(node, dict):
                return int(node.get("min", default[0])), int(node.get("max", default[1]))
            if isinstance(node, int):
                return node, node
            return default

        return cls(
            modules=tuple(cfg.get("league.allowed_modules", list(cls.modules)) or ()),
            bench_size=int(cfg.get("league.bench_size", 14)),
            modificatore_difesa=bool(cfg.get("league.modifiers.modificatore_difesa", False)),
            score_starter=float(cfg.get("lineup.scores.starter", 100.0)),
            score_doubt=float(cfg.get("lineup.scores.doubt", 45.0)),
            score_bench=float(cfg.get("lineup.scores.bench", 8.0)),
            score_unknown=float(cfg.get("lineup.scores.unknown", 25.0)),
            score_out=float(cfg.get("lineup.scores.out", -1000.0)),
            w_probabilita=float(cfg.get("lineup.weights.probabilita", 25.0)),
            w_consenso=float(cfg.get("lineup.weights.consenso", 15.0)),
            w_fantamedia=float(cfg.get("lineup.weights.fantamedia", 8.0)),
            w_ordine_rosa=float(cfg.get("lineup.weights.ordine_rosa", 0.5)),
            pen_squadra_ferma=float(cfg.get("lineup.penalties.squadra_non_in_campo", 500.0)),
            pen_dubbio=float(cfg.get("lineup.penalties.per_dubbio_schierato", 12.0)),
            bonus_difesa_4=float(
                cfg.get("lineup.module_bonus.difesa_a_4_con_modificatore", 60.0)
            ),
            bonus_difesa_5=float(
                cfg.get("lineup.module_bonus.difesa_a_5_con_modificatore", 25.0)
            ),
            module_preferences=dict(cfg.get("lineup.module_bonus.preferenze", {}) or {}),
            allow_incomplete=bool(cfg.get("lineup.allow_incomplete_lineup", True)),
            bench_strategy=str(cfg.get("lineup.bench_strategy", "per_ruolo_poi_punteggio")),
            tiebreakers=tuple(cfg.get("aggregation.tiebreakers", []) or ()),
            slot_limits={
                "D": limits("difensori", (3, 5)),
                "C": limits("centrocampisti", (3, 5)),
                "A": limits("attaccanti", (1, 3)),
            },
        )


# --------------------------------------------------------------------------
# Punteggio del singolo giocatore
# --------------------------------------------------------------------------

_BASE_BY_STATUS = {
    Status.STARTER: "score_starter",
    Status.DOUBT: "score_doubt",
    Status.BENCH: "score_bench",
    Status.UNKNOWN: "score_unknown",
    Status.OUT: "score_out",
}


def score_player(verdict: PlayerVerdict, settings: LineupSettings) -> ScoredPlayer:
    """Punteggio di un giocatore, con il dettaglio di come si compone."""
    breakdown: dict[str, float] = {}

    base = getattr(settings, _BASE_BY_STATUS[verdict.status])
    breakdown["stato"] = base

    if verdict.probability is not None:
        breakdown["probabilita"] = (verdict.probability / 100.0) * settings.w_probabilita

    breakdown["consenso"] = verdict.consensus * settings.w_consenso

    if verdict.player.fantamedia is not None:
        breakdown["fantamedia"] = (verdict.player.fantamedia / 10.0) * settings.w_fantamedia

    # Piu' in alto in rosa = spinta leggermente maggiore, a parita' di tutto.
    breakdown["ordine_rosa"] = -verdict.player.order * settings.w_ordine_rosa

    if not verdict.team_playing:
        breakdown["squadra_ferma"] = -settings.pen_squadra_ferma

    # Spareggio deterministico fra pari merito (criteri da config).
    breakdown["spareggio"] = resolve_doubt(verdict, settings.tiebreakers) * 1e-3

    return ScoredPlayer(verdict=verdict, score=sum(breakdown.values()), breakdown=breakdown)


# --------------------------------------------------------------------------
# Scelta del modulo e composizione
# --------------------------------------------------------------------------


def build_lineup(verdicts: list[PlayerVerdict], settings: LineupSettings) -> Lineup:
    """Sceglie modulo e undici titolari, e ordina la panchina."""
    scored = [score_player(v, settings) for v in verdicts]

    # Gli indisponibili non entrano mai fra i titolari: sono esclusi qui, non
    # tramite un punteggio molto negativo, cosi' il conteggio dei titolari
    # riflette i giocatori realmente schierabili.
    available = [p for p in scored if p.verdict.is_startable]
    excluded = [p for p in scored if not p.verdict.is_startable]

    by_role: dict[Role, list[ScoredPlayer]] = {role: [] for role in Role}
    for player in available:
        by_role[player.role].append(player)
    for role in by_role:
        by_role[role].sort(key=lambda p: p.score, reverse=True)

    modules = [_safe_module(name) for name in settings.modules]
    modules = [m for m in modules if m is not None and _module_allowed(m, settings)]
    if not modules:
        raise LineupError("nessun modulo ammesso e' utilizzabile: controlla config.yaml")

    warnings: list[str] = []
    candidates: list[tuple[float, Module, list[ScoredPlayer], int]] = []

    for module in modules:
        picked: list[ScoredPlayer] = []
        missing = 0
        for role, needed in module.slots().items():
            pool = by_role[role]
            picked.extend(pool[:needed])
            missing += max(0, needed - len(pool))

        if missing and not settings.allow_incomplete:
            continue

        doubts = sum(1 for p in picked if p.verdict.status is Status.DOUBT)
        total = sum(p.score for p in picked)
        total -= doubts * settings.pen_dubbio
        total += _module_bonus(module, settings)
        # Un modulo che non riusciamo a riempire e' peggiore di uno completo.
        total -= missing * 1000.0
        candidates.append((total, module, picked, missing))

    if not candidates:
        raise LineupError("rosa insufficiente per qualunque modulo ammesso")

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_module, starters, missing = candidates[0]

    if missing:
        warnings.append(
            f"rosa incompleta: mancano {missing} giocatori per riempire {best_module.name}"
        )

    starters = _sort_by_role(starters)
    bench = _build_bench(available, starters, settings)

    lineup = Lineup(
        module=best_module.name,
        starters=starters,
        bench=bench,
        module_score=best_score,
        module_scores={m.name: s for s, m, _, _ in candidates},
        warnings=warnings,
    )
    lineup.decisions = _decisions(lineup, excluded, by_role, settings)
    return lineup


def _safe_module(name: str) -> Module | None:
    try:
        return Module.parse(name)
    except LineupError as exc:
        log.warning("modulo ignorato: %s", exc)
        return None


def _module_allowed(module: Module, settings: LineupSettings) -> bool:
    """Controlla il modulo contro i limiti di reparto del regolamento."""
    checks = {
        "D": module.difensori,
        "C": module.centrocampisti,
        "A": module.attaccanti,
    }
    for role, count in checks.items():
        low, high = settings.slot_limits.get(role, (0, 11))
        if not low <= count <= high:
            log.warning("modulo %s fuori dai limiti di reparto (%s: %d non in %d-%d)",
                        module.name, role, count, low, high)
            return False
    return True


def _module_bonus(module: Module, settings: LineupSettings) -> float:
    bonus = settings.module_preferences.get(module.name, 0.0)
    if settings.modificatore_difesa:
        if module.difensori == 4:
            bonus += settings.bonus_difesa_4
        elif module.difensori == 5:
            bonus += settings.bonus_difesa_5
    return float(bonus)


_ROLE_ORDER = {Role.P: 0, Role.D: 1, Role.C: 2, Role.A: 3}


def _sort_by_role(players: list[ScoredPlayer]) -> list[ScoredPlayer]:
    return sorted(players, key=lambda p: (_ROLE_ORDER[p.role], -p.score))


def _build_bench(
    available: list[ScoredPlayer], starters: list[ScoredPlayer], settings: LineupSettings
) -> list[ScoredPlayer]:
    """Panchina in ordine di subentro.

    Con `per_ruolo_poi_punteggio` la panchina e' raggruppata per ruolo (P, D, C,
    A) e ordinata per punteggio dentro ogni gruppo: e' l'ordine che serve al
    subentro automatico, che sostituisce un titolare con una riserva del suo
    stesso ruolo.
    """
    chosen = {id(p) for p in starters}
    rest = [p for p in available if id(p) not in chosen]

    if settings.bench_strategy == "solo_punteggio":
        rest.sort(key=lambda p: p.score, reverse=True)
    else:
        rest = _sort_by_role(rest)

    return rest[: settings.bench_size]


def _decisions(
    lineup: Lineup,
    excluded: list[ScoredPlayer],
    by_role: dict[Role, list[ScoredPlayer]],
    settings: LineupSettings,
) -> list[str]:
    """Righe leggibili che spiegano le scelte, per il messaggio Telegram."""
    lines: list[str] = []

    ranking = sorted(lineup.module_scores.items(), key=lambda kv: kv[1], reverse=True)
    if len(ranking) > 1:
        runner_up, runner_score = ranking[1]
        lines.append(
            f"Modulo {lineup.module} scelto su {runner_up} "
            f"({ranking[0][1]:.0f} vs {runner_score:.0f} punti)"
        )
    if settings.modificatore_difesa:
        difensori = len(lineup.by_role(Role.D))
        lines.append(
            f"Modificatore difesa attivo: schierati {difensori} difensori"
        )

    for player in lineup.starters:
        if player.verdict.status is Status.DOUBT:
            lines.append(
                f"Dubbio risolto a favore di {player.player.name} "
                f"({player.player.team}): {player.verdict.note}"
            )
        elif player.verdict.status is Status.UNKNOWN:
            lines.append(
                f"{player.player.name} ({player.player.team}) schierato senza "
                "riscontri nelle probabili: nessuna alternativa migliore nel ruolo"
            )

    for player in excluded:
        replacement = _replacement_for(player, lineup)
        reason = player.verdict.out_reason or "indisponibile"
        if replacement is not None:
            lines.append(
                f"{player.player.name} escluso ({reason}) -> dentro {replacement}"
            )
        else:
            lines.append(f"{player.player.name} escluso ({reason})")

    for player in lineup.starters:
        if not player.verdict.team_playing:
            lines.append(
                f"Attenzione: {player.player.name} ({player.player.team}) "
                "non risulta in campo in questa giornata"
            )

    return lines


def _replacement_for(excluded: ScoredPlayer, lineup: Lineup) -> str | None:
    """Chi occupa, nello stesso ruolo, il posto che sarebbe stato dell'escluso."""
    same_role = lineup.by_role(excluded.role)
    if not same_role:
        return None
    return same_role[-1].player.name
