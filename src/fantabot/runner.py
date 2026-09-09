"""Orchestrazione di un run completo.

Sequenza:
 1. login sulla lega e lettura della rosa
 2. lettura del regolamento (se `autodetect_rules`) per tarare moduli/modificatori
 3. calcolo della deadline di giornata
 4. scraping e aggregazione delle probabili dalle fonti abilitate
 5. calcolo della formazione ottimale
 6. submit (saltato in DRY_RUN)
 7. notifica Telegram

Ogni errore critico manda subito un alert Telegram e poi rialza, cosi' il job
CI risulta rosso e resta traccia dell'artifact di debug.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fantabot.aggregate import AggregationSettings, Aggregator
from fantabot.config import Config, Secrets
from fantabot.deadline import SerieACalendar, effective_deadline
from fantabot.http import client_from_config
from fantabot.lega.client import LeagueClient, LeagueError, load_selectors
from fantabot.lineup import LineupSettings, build_lineup
from fantabot.models import Matchday, RosterPlayer, RunResult, SourceReport
from fantabot.names import AliasMap, resolve_team
from fantabot.notify import TelegramNotifier
from fantabot.sources import REGISTRY, SourceContext
from fantabot.sources.unavailability import UnavailabilityFeed, Unavailable
from fantabot.stats import StatsBook, build_form_guide, fetch_stats

log = logging.getLogger(__name__)


def _resolve_roster_teams(
    roster: list[RosterPlayer], teams_playing: set[str], aliases: AliasMap
) -> list[RosterPlayer]:
    """Porta le squadre della rosa nella stessa forma usata da fonti e calendario.

    La lega scrive la squadra come sigla (`JUV`); fonti e calendario usano il
    nome completo (`Juventus`). Senza allinearle nessun giocatore combacia con
    le probabili e ognuno prende il malus di "squadra non in campo".
    """
    if not teams_playing:
        return roster

    risolti: list[RosterPlayer] = []
    cambiati = 0
    non_risolti: set[str] = set()
    for player in roster:
        team = resolve_team(player.team, teams_playing, aliases)
        if team != player.team:
            cambiati += 1
            risolti.append(replace(player, team=team))
            continue
        if player.team and team not in teams_playing:
            non_risolti.add(player.team)
        risolti.append(player)

    if cambiati:
        log.info("squadre della rosa risolte dalla sigla al nome completo: %d", cambiati)
    if non_risolti:
        # Non e' per forza un errore: puo' essere una squadra che riposa.
        log.warning("sigle non riconosciute fra le squadre di giornata: %s",
                    ", ".join(sorted(non_risolti)))
    return risolti


def _with_fantamedia(roster: list[RosterPlayer], guide) -> list[RosterPlayer]:
    """Porta la fantamedia dalle statistiche dentro la rosa.

    La pagina della lega non la espone, quindi finora il criterio di spareggio
    "fantamedia" non aveva mai un numero su cui lavorare: era configurato e
    silenziosamente inerte.
    """
    if not guide:
        return roster

    arricchiti: list[RosterPlayer] = []
    trovate = 0
    for player in roster:
        stat = guide.stats_for(player.name)
        if stat is None or stat.fantamedia is None or player.fantamedia is not None:
            arricchiti.append(player)
            continue
        arricchiti.append(replace(player, fantamedia=stat.fantamedia))
        trovate += 1

    if trovate:
        log.info("fantamedia trovata per %d giocatori della rosa", trovate)
    return arricchiti


class RunAborted(RuntimeError):
    """Interruzione non-critica: non c'e' niente da fare adesso (non e' un errore)."""


class Runner:
    def __init__(self, cfg: Config, secrets: Secrets, *, force: bool = False,
                 headless: bool = True, capture_api: bool = False) -> None:
        self.cfg = cfg
        self.secrets = secrets
        self.force = force
        self.headless = headless
        self.capture_api = capture_api
        self.notifier = TelegramNotifier(
            token=secrets.telegram_token,
            chat_id=secrets.telegram_chat_id,
            enabled=bool(cfg.get("telegram.enabled", True)),
            parse_mode=str(cfg.get("telegram.parse_mode", "HTML")),
        )

    # -- entry point --------------------------------------------------------

    def run(self) -> RunResult:
        result = RunResult(ok=False, dry_run=self.cfg.dry_run, started_at=datetime.now(UTC))
        try:
            self._run_inner(result)
            result.ok = True
        except RunAborted as exc:
            log.info("run interrotto: %s", exc)
            result.ok = True
            result.submit_detail = str(exc)
            result.finished_at = datetime.now(UTC)
            return result
        except Exception as exc:  # noqa: BLE001 - qualunque errore va notificato
            log.exception("run fallito")
            result.error = f"{type(exc).__name__}: {exc}"
            self.notifier.send_alert("Fantabot: run fallito", result.error)
            result.finished_at = datetime.now(UTC)
            raise
        finally:
            if result.finished_at is None:
                result.finished_at = datetime.now(UTC)

        if self.cfg.get("telegram.notify_on_success", True):
            self._notify(result)
        return result

    # -- passi --------------------------------------------------------------

    def _run_inner(self, result: RunResult) -> None:
        slug = self.cfg.league_slug(self.secrets)
        if not slug:
            raise LeagueError(
                "slug della lega mancante: valorizza FANTACALCIO_LEAGUE_SLUG "
                "oppure league.slug in config.yaml"
            )
        if not self.secrets.has_credentials:
            raise LeagueError(
                "credenziali mancanti: servono FANTACALCIO_USERNAME e FANTACALCIO_PASSWORD"
            )

        out_dir = self.cfg.output_dir
        raw_dir = out_dir / "raw" if self.cfg.get("run.save_raw_html", True) else None

        with LeagueClient(
            slug=slug,
            username=self.secrets.username or "",
            password=self.secrets.password or "",
            team_id=self.cfg.team_id(self.secrets),
            selectors=load_selectors(),
            headless=self.headless,
            artifacts_dir=out_dir / "lega",
            diagnostics_dir=out_dir,
            capture_api=self.capture_api,
            use_api=bool(self.cfg.get("league.use_api", True)),
            timezone=str(self.cfg.get("deadline.timezone", "Europe/Rome")),
        ) as lega:
            lega.login()

            if self.cfg.get("league.autodetect_rules", True):
                changes = lega.read_rules().applied_to(self.cfg)
                for change in changes:
                    log.info("regolamento letto dalla lega -> %s", change)

            roster = lega.read_roster()

            matchday = self._resolve_matchday(lega)
            result.matchday = matchday
            self._check_deadline(matchday)

            aliases = AliasMap.load(
                self.cfg.get("aggregation.matching.alias_file", "config/aliases.yaml")
            )
            roster = _resolve_roster_teams(roster, matchday.teams, aliases)

            reports, unavailable, stats = self._collect_sources(raw_dir)
            result.sources = reports

            healthy = [r for r in reports if r.ok]
            min_sources = int(self.cfg.get("sources.min_sources", 1))
            if len(healthy) < min_sources:
                names = ", ".join(f"{r.source} ({r.error})" for r in reports if not r.ok)
                raise RuntimeError(
                    f"solo {len(healthy)} fonti disponibili su {min_sources} richieste: {names}"
                )

            guide = build_form_guide(stats, matchday=matchday, roster=roster,
                                     aliases=aliases)
            if stats.errors:
                log.warning("statistiche parziali: %s", "; ".join(stats.errors))
            roster = _with_fantamedia(roster, guide)

            verdicts = Aggregator(
                settings=AggregationSettings.from_config(self.cfg),
                aliases=AliasMap.load(
                    self.cfg.get("aggregation.matching.alias_file", "config/aliases.yaml")
                ),
                source_weights=self._source_weights(),
            ).aggregate(
                roster=roster,
                reports=reports,
                unavailable=unavailable,
                teams_playing=matchday.teams if matchday.teams else None,
            )

            lineup = build_lineup(verdicts, LineupSettings.from_config(self.cfg), guide)
            result.lineup = lineup
            log.info("formazione scelta: %s", lineup.module)

            try:
                detail = lega.submit_lineup(lineup, dry_run=self.cfg.dry_run)
                result.submit_detail = detail
                result.submitted = not self.cfg.dry_run
            finally:
                # Anche se il submit fallisce il registro va salvato: e'
                # proprio la chiamata fallita quella che interessa vedere.
                lega.record_storage_keys()
                lega.save_api_trace()

    def _resolve_matchday(self, lega: LeagueClient) -> Matchday:
        with client_from_config(self.cfg, cache_dir=Path(".cache-http")) as client:
            calendar = SerieACalendar(
                client=client,
                url=str(self.cfg.get("deadline.calendar_url",
                                     "https://www.fantacalcio.it/serie-a/calendario")),
                tz=str(self.cfg.get("deadline.timezone", "Europe/Rome")),
            )
            matchday = calendar.fetch_matchday()

        if self.cfg.get("deadline.prefer_league_deadline", True):
            league_deadline = lega.read_deadline()
            if league_deadline is not None:
                log.info("deadline letta dalla lega: %s (calendario: %s)",
                         league_deadline, matchday.deadline)
                matchday.deadline = league_deadline
                matchday.deadline_source = "lega"
        return matchday

    def _check_deadline(self, matchday: Matchday) -> None:
        margin = int(self.cfg.get("deadline.safety_margin_minutes", 20))
        deadline = effective_deadline(matchday, margin)
        if deadline is None:
            log.warning("deadline sconosciuta: proseguo comunque")
            return

        now = datetime.now(deadline.tzinfo)
        if now >= deadline:
            if self.force:
                log.warning("deadline superata (%s) ma --force attivo: proseguo", deadline)
                return
            raise RunAborted(
                f"deadline gia' superata ({deadline:%d/%m %H:%M}): non schiero nulla. "
                "Usa --force per forzare."
            )

        max_hours = float(self.cfg.get("deadline.skip_if_more_than_hours_before", 60))
        if not self.force and now < deadline - timedelta(hours=max_hours):
            raise RunAborted(
                f"mancano piu' di {max_hours:.0f}h alla deadline ({deadline:%d/%m %H:%M}): "
                "le probabili non sono ancora affidabili, riprovo al prossimo run."
            )
        log.info("deadline effettiva %s (mancano %.1f ore)", deadline,
                 (deadline - now).total_seconds() / 3600)

    def _collect_sources(
        self, raw_dir: Path | None
    ) -> tuple[list[SourceReport], list[Unavailable], StatsBook]:
        reports: list[SourceReport] = []
        unavailable: list[Unavailable] = []
        stats = StatsBook()

        with client_from_config(self.cfg, cache_dir=Path(".cache-http")) as client:
            ctx = SourceContext(client=client, config=self.cfg, raw_dir=raw_dir)
            for key, cls in REGISTRY.items():
                if not self.cfg.get(f"sources.{key}.enabled", True):
                    log.info("fonte %s disabilitata da config", key)
                    continue
                reports.append(cls(ctx).fetch())

            if self.cfg.get("sources.unavailability.enabled", True):
                url = str(self.cfg.get("sources.unavailability.infortunati_url",
                                       "https://www.fantacalcio.it/indisponibili-serie-a"))
                try:
                    unavailable = UnavailabilityFeed(client, url).fetch()
                except Exception as exc:  # noqa: BLE001 - non blocca il run
                    log.warning("elenco indisponibili non disponibile: %s", exc)

            if self.cfg.get("statistiche.enabled", True):
                # Due richieste sole, sullo stesso client gentile delle fonti.
                stats = fetch_stats(
                    client,
                    classifica_url=str(self.cfg.get(
                        "statistiche.classifica_url",
                        "https://www.fantacalcio.it/serie-a/classifica")),
                    statistiche_url=str(self.cfg.get(
                        "statistiche.statistiche_url",
                        "https://www.fantacalcio.it/statistiche-serie-a")),
                    save_raw=ctx.save_raw,
                )

        return reports, unavailable, stats

    def _source_weights(self) -> dict[str, float]:
        """Pesi per etichetta di fonte (le fonti votano con la loro `label`)."""
        weights: dict[str, float] = {}
        for key, cls in REGISTRY.items():
            weights[cls.label] = float(self.cfg.get(f"sources.{key}.weight", 1.0))
        return weights

    def _notify(self, result: RunResult) -> None:
        from fantabot.notify import format_result

        text = format_result(
            result,
            include_decisions=bool(self.cfg.get("telegram.include_decisions", True)),
            include_bench=bool(self.cfg.get("telegram.include_bench", False)),
        )
        self.notifier.send(text)
