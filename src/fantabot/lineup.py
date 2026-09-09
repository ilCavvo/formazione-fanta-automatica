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
    w_ordine_rosa: float = 0.5

    # -- chi si affronta ---------------------------------------------------
    #: Quanto pesa l'avversario, per ruolo. Il portiere e' il piu' esposto:
    #: subisce l'attacco avversario per intero e non ha bonus con cui rifarsi.
    w_avversario: dict[str, float] = field(
        default_factory=lambda: {"P": 10.0, "D": 5.0, "C": 3.0, "A": 4.0}
    )
    #: Oltre questo scarto dalla media di campionato non si guarda: a inizio
    #: stagione tre partite bastano a produrre numeri che non significano nulla.
    max_scarto_avversario: float = 1.5
    w_forma_squadra: float = 4.0
    w_forma_avversario: float = 3.0

    # -- come sta andando --------------------------------------------------
    #: Media voto, misurata rispetto alla sufficienza.
    w_media_voto: float = 12.0
    #: Bonus e malus medi a partita (fantamedia meno media voto).
    w_bonus: float = 14.0
    max_scarto_bonus: float = 2.5
    #: Sotto questo numero di partite le medie sono rumore: non si usano.
    min_partite: int = 2
    #: Tetto complessivo dei contributi statistici, in valore assoluto.
    #: Zero = nessun tetto, ed e' il default: un giocatore forte che gioca
    #: sempre puo' battere un titolare scarso anche quando le probabili lo
    #: danno in panchina. Serve che sia possibile, non che sia frequente.
    #: Chi non e' disponibile (infortunato, squalificato) resta fuori a
    #: prescindere: e' escluso prima del punteggio, non con un numero basso.
    max_totale_statistiche: float = 0.0

    pen_squadra_ferma: float = 500.0
    pen_dubbio: float = 12.0

    bonus_difesa_4: float = 60.0
    bonus_difesa_5: float = 25.0
    module_preferences: dict[str, float] = field(default_factory=dict)

    allow_incomplete: bool = True
    bench_strategy: str = "punteggio_portieri_in_fondo"
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
            w_ordine_rosa=float(cfg.get("lineup.weights.ordine_rosa", 0.5)),
            w_avversario={
                ruolo: float(
                    (cfg.get("lineup.weights.avversario", {}) or {}).get(ruolo, default)
                )
                for ruolo, default in (("P", 10.0), ("D", 5.0), ("C", 3.0), ("A", 4.0))
            },
            max_scarto_avversario=float(
                cfg.get("lineup.weights.max_scarto_avversario", 1.5)
            ),
            w_forma_squadra=float(cfg.get("lineup.weights.forma_squadra", 4.0)),
            w_forma_avversario=float(cfg.get("lineup.weights.forma_avversario", 3.0)),
            w_media_voto=float(cfg.get("lineup.weights.media_voto", 12.0)),
            w_bonus=float(cfg.get("lineup.weights.bonus", 14.0)),
            max_scarto_bonus=float(cfg.get("lineup.weights.max_scarto_bonus", 2.5)),
            min_partite=int(cfg.get("lineup.weights.min_partite", 2)),
            max_totale_statistiche=float(
                cfg.get("lineup.weights.max_totale_statistiche", 0.0)
            ),
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
            bench_strategy=str(cfg.get("lineup.bench_strategy", "punteggio_portieri_in_fondo")),
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


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _capped(terms: dict[str, float], limit: float) -> dict[str, float]:
    """Riduce in proporzione i contributi statistici se sforano il tetto.

    Si riduce invece di tagliare cosi' il dettaglio resta leggibile: si vede
    ancora quanto ha pesato l'avversario rispetto ai bonus, in scala.
    """
    if not terms or limit <= 0:
        return terms
    totale = sum(terms.values())
    if abs(totale) <= limit:
        return terms
    fattore = limit / abs(totale)
    return {nome: valore * fattore for nome, valore in terms.items()}


def _opponent_terms(verdict: PlayerVerdict, settings: LineupSettings,
                    guide) -> dict[str, float]:
    """Quanto pesa, per questo giocatore, la squadra che affronta.

    Il metro cambia con il ruolo, perche' cambia cosa fa male:

    - portiere e difensori subiscono l'**attacco** avversario: piu' gol fa
      l'avversario, meno vale schierarli. E' la regola chiesta, e il portiere
      la sente per intero perche' non ha bonus con cui compensare;
    - centrocampisti e attaccanti trovano davanti la **difesa** avversaria: una
      difesa che incassa poco vale un punteggio minore, una che incassa tanto
      apre al bonus.

    Tutto e' misurato come scarto dalla media del campionato, cosi' un
    avversario nella norma non sposta niente invece di spostare tutti.
    """
    peso = settings.w_avversario.get(str(verdict.player.role), 0.0)
    if guide is None or not peso:
        return {}

    squadra = verdict.player.team
    avversario = guide.opponent_of(squadra)
    if avversario is None or avversario.played < settings.min_partite:
        return {}

    terms: dict[str, float] = {}
    book = guide.book

    if verdict.player.role in (Role.P, Role.D):
        forza, media = avversario.attack, book.average_attack
        verso = -1.0
    else:
        forza, media = avversario.defence, book.average_defence
        verso = +1.0

    if forza is not None and media is not None:
        scarto = _clamp(forza - media, settings.max_scarto_avversario)
        terms["avversario"] = verso * scarto * peso

    if settings.w_forma_avversario and book.average_form is not None:
        punti = avversario.form_points
        if punti is not None:
            scarto = _clamp(punti - book.average_form, settings.max_scarto_avversario)
            # Anche la forma dell'avversario segue il dosaggio per ruolo: se
            # cosi' non fosse, peserebbe uguale su tutti e annullerebbe la
            # distinzione fra il portiere e gli altri.
            massimo = max(settings.w_avversario.values() or [1.0]) or 1.0
            terms["forma_avversario"] = (
                -scarto * settings.w_forma_avversario * (peso / massimo)
            )

    if settings.w_forma_squadra and book.average_form is not None:
        mia = guide.team_of(squadra)
        if mia is not None and mia.played >= settings.min_partite:
            punti = mia.form_points
            if punti is not None:
                scarto = _clamp(punti - book.average_form, settings.max_scarto_avversario)
                terms["forma_squadra"] = scarto * settings.w_forma_squadra

    return terms


def _form_terms(verdict: PlayerVerdict, settings: LineupSettings,
                guide) -> dict[str, float]:
    """Come sta andando il giocatore: i voti che prende e i bonus che porta.

    Sono due cose distinte e vanno pesate separatamente. La fantamedia non ha
    un peso suo proprio perche' **e' la loro somma**: darle un peso in piu'
    significherebbe contare due volte le stesse partite.
    """
    if guide is None:
        return {}
    stat = guide.stats_for(verdict.player.name)
    if stat is None or stat.played < settings.min_partite:
        return {}

    terms: dict[str, float] = {}
    if stat.media_voto is not None and settings.w_media_voto:
        # Misurata sulla sufficienza: il 6 e' il punto neutro del voto.
        terms["media_voto"] = (stat.media_voto - 6.0) * settings.w_media_voto

    bonus = stat.bonus_per_match
    if bonus is not None and settings.w_bonus:
        terms["bonus"] = _clamp(bonus, settings.max_scarto_bonus) * settings.w_bonus

    return terms


def score_player(verdict: PlayerVerdict, settings: LineupSettings,
                 guide=None) -> ScoredPlayer:
    """Punteggio di un giocatore, con il dettaglio di come si compone."""
    breakdown: dict[str, float] = {}

    base = getattr(settings, _BASE_BY_STATUS[verdict.status])
    breakdown["stato"] = base

    if verdict.probability is not None:
        breakdown["probabilita"] = (verdict.probability / 100.0) * settings.w_probabilita

    breakdown["consenso"] = verdict.consensus * settings.w_consenso

    statistiche = {**_form_terms(verdict, settings, guide),
                   **_opponent_terms(verdict, settings, guide)}
    breakdown.update(_capped(statistiche, settings.max_totale_statistiche))

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


def build_lineup(verdicts: list[PlayerVerdict], settings: LineupSettings,
                 guide=None) -> Lineup:
    """Sceglie modulo e undici titolari, e ordina la panchina.

    `guide` e' la `FormGuide` con statistiche e avversari di giornata: se manca
    (fonte irraggiungibile) il calcolo prosegue con le sole probabili.
    """
    scored = [score_player(v, settings, guide) for v in verdicts]

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
    lineup.decisions = _decisions(lineup, excluded, by_role, settings, guide)
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

    Tre ordinamenti, scelti da `lineup.bench_strategy`:

    - `punteggio_portieri_in_fondo` (default): dal punteggio piu' alto al piu'
      basso, con i portieri sempre in coda. E' l'ordine giusto perche' il
      subentro pesca il primo della lista utile: davanti va chi rende di piu',
      mentre un portiere di riserva serve solo nel caso raro in cui il titolare
      non giochi, e messo in alto ruberebbe il posto a chi puo' entrare
      davvero;
    - `solo_punteggio`: punteggio puro, portieri compresi;
    - `per_ruolo_poi_punteggio`: raggruppata per ruolo P, D, C, A.
    """
    chosen = {id(p) for p in starters}
    rest = [p for p in available if id(p) not in chosen]

    if settings.bench_strategy == "solo_punteggio":
        rest.sort(key=lambda p: p.score, reverse=True)
    elif settings.bench_strategy == "per_ruolo_poi_punteggio":
        rest = _sort_by_role(rest)
    else:
        rest.sort(key=lambda p: (p.role is Role.P, -p.score))

    return rest[: settings.bench_size]


def _decisions(
    lineup: Lineup,
    excluded: list[ScoredPlayer],
    by_role: dict[Role, list[ScoredPlayer]],
    settings: LineupSettings,
    guide=None,
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

    lines.extend(_opponent_lines(lineup, guide))

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


def _opponent_lines(lineup: Lineup, guide) -> list[str]:
    """Spiega l'incontro dove ha spostato davvero il punteggio.

    Il portiere per primo e sempre, perche' e' il ruolo dove la scelta si gioca
    quasi tutta sull'avversario; degli altri si nomina solo chi ha ricevuto una
    spinta sensibile, altrimenti il messaggio diventa un elenco che nessuno
    legge.
    """
    if guide is None:
        return []

    lines: list[str] = []
    for player in lineup.starters:
        peso = player.breakdown.get("avversario")
        if peso is None:
            continue
        avversario = guide.opponent_name(player.player.team)
        if not avversario:
            continue
        stats = guide.opponent_of(player.player.team)
        if player.role is Role.P and stats is not None and stats.attack is not None:
            lines.append(
                f"{player.player.name} contro {avversario}, che segna "
                f"{stats.attack:.1f} gol a partita ({peso:+.0f} punti)"
            )
        elif abs(peso) >= 3.0:
            lines.append(
                f"{player.player.name} contro {avversario} ({peso:+.0f} punti)"
            )
    return lines


def _replacement_for(excluded: ScoredPlayer, lineup: Lineup) -> str | None:
    """Chi occupa, nello stesso ruolo, il posto che sarebbe stato dell'escluso."""
    same_role = lineup.by_role(excluded.role)
    if not same_role:
        return None
    return same_role[-1].player.name
