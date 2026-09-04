"""Interfaccia a riga di comando.

    fantabot run                 # run completo (DRY_RUN da config)
    fantabot run --no-dry-run    # schiera davvero
    fantabot probabili           # solo scraping+aggregazione, senza login
    fantabot deadline            # quando scade la prossima giornata
    fantabot discover            # mappa le pagine della lega (report sicuro)
    fantabot inspect             # HTML+screenshot della lega (solo in locale)
    fantabot notify-test         # verifica che il bot Telegram funzioni
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from fantabot.config import Config, Secrets
from fantabot.deadline import SerieACalendar, effective_deadline
from fantabot.http import client_from_config
from fantabot.logging_setup import setup_logging
from fantabot.notify import TelegramNotifier
from fantabot.runner import Runner
from fantabot.sources import REGISTRY, SourceContext

log = logging.getLogger("fantabot")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fantabot", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config/config.yaml",
                        help="percorso del file di configurazione")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING...")

    dry = parser.add_mutually_exclusive_group()
    dry.add_argument("--dry-run", dest="dry_run", action="store_true", default=None,
                     help="calcola e notifica ma non invia la formazione")
    dry.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                     help="invia davvero la formazione al sito")

    parser.add_argument("--force", action="store_true",
                        help="ignora i controlli sulla deadline")
    parser.add_argument("--headful", action="store_true",
                        help="mostra il browser (debug locale)")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run completo")
    sub.add_parser("probabili", help="solo fonti + aggregazione, nessun login")
    sub.add_parser("deadline", help="mostra la deadline della prossima giornata")
    sub.add_parser("discover", help="mappa le pagine della lega (report pubblicabile)")
    sub.add_parser("inspect", help="scarica HTML e screenshot della lega (solo locale)")
    sub.add_parser("notify-test", help="manda un messaggio di prova su Telegram")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    cfg = Config.load(args.config)
    if args.dry_run is not None:
        # La CLI vince sul file di config, ma non sull'env var (che sta piu' in
        # basso nella catena e serve alla CI): la allineiamo esplicitamente.
        os.environ["DRY_RUN"] = "true" if args.dry_run else "false"

    level = args.log_level or str(cfg.get("logging.level", "INFO"))
    log_path = setup_logging(level, cfg.output_dir, str(cfg.get("logging.file",
                                                                "fantabot.log")))
    if log_path:
        log.info("log dettagliato: %s", log_path)

    secrets = Secrets.from_env()
    handlers = {
        "run": _cmd_run,
        "probabili": _cmd_probabili,
        "deadline": _cmd_deadline,
        "discover": _cmd_discover,
        "inspect": _cmd_inspect,
        "notify-test": _cmd_notify_test,
    }
    return handlers[args.command](cfg, secrets, args)


# -- comandi ---------------------------------------------------------------


def _cmd_run(cfg: Config, secrets: Secrets, args) -> int:
    runner = Runner(cfg, secrets, force=args.force, headless=not args.headful)
    try:
        result = runner.run()
    except Exception as exc:  # noqa: BLE001 - gia' notificato dal runner
        log.error("run fallito: %s", exc)
        return 1

    _dump_result(cfg, result)
    if result.lineup is not None:
        print(f"\nModulo: {result.lineup.module}")
        for player in result.lineup.starters:
            print(f"  {player.role.value} {player.player.name:22s} "
                  f"{player.verdict.status.value:14s} {player.score:7.1f}")
    print(f"\n{result.submit_detail or ''}")
    return 0 if result.ok else 1


def _cmd_probabili(cfg: Config, secrets: Secrets, args) -> int:
    """Utile per verificare le fonti senza toccare l'account della lega."""
    raw_dir = cfg.output_dir / "raw" if cfg.get("run.save_raw_html", True) else None
    with client_from_config(cfg, cache_dir=Path(".cache-http")) as client:
        ctx = SourceContext(client=client, config=cfg, raw_dir=raw_dir)
        for key, cls in REGISTRY.items():
            if not cfg.get(f"sources.{key}.enabled", True):
                print(f"- {cls.label}: disabilitata")
                continue
            report = cls(ctx).fetch()
            if report.ok:
                starters = sum(1 for e in report.entries if e.status.value == "titolare")
                print(f"- {cls.label}: {len(report.entries)} giocatori, "
                      f"{starters} titolari, {len(report.teams)} squadre")
            else:
                print(f"- {cls.label}: NON DISPONIBILE ({report.error})")
    return 0


def _cmd_deadline(cfg: Config, secrets: Secrets, args) -> int:
    with client_from_config(cfg, cache_dir=Path(".cache-http")) as client:
        calendar = SerieACalendar(
            client=client,
            url=str(cfg.get("deadline.calendar_url")),
            tz=str(cfg.get("deadline.timezone", "Europe/Rome")),
        )
        matchday = calendar.fetch_matchday()

    margin = int(cfg.get("deadline.safety_margin_minutes", 20))
    effective = effective_deadline(matchday, margin)
    print(f"Giornata {matchday.matchweek} — {len(matchday.matches)} partite")
    print(f"Primo anticipo: {matchday.deadline}")
    print(f"Deadline effettiva (margine {margin} min): {effective}")
    if effective is not None:
        remaining = (effective - datetime.now(effective.tzinfo)).total_seconds() / 3600
        print(f"Mancano {remaining:.1f} ore")
    return 0


def _cmd_inspect(cfg: Config, secrets: Secrets, args) -> int:
    from fantabot.lega.client import LeagueClient, load_selectors

    slug = cfg.league_slug(secrets)
    if not slug or not secrets.has_credentials:
        log.error("servono credenziali e slug della lega per l'inspect")
        return 1

    out = cfg.output_dir / "inspect"
    with LeagueClient(
        slug=slug,
        username=secrets.username or "",
        password=secrets.password or "",
        team_id=cfg.team_id(secrets),
        selectors=load_selectors(),
        headless=not args.headful,
        artifacts_dir=out,
    ) as lega:
        lega.login()
        saved = lega.inspect()

    print(f"Pagine salvate in {out}:")
    for path in saved:
        print(f"  {path}")
    print("\nApri gli HTML, individua i selettori giusti e aggiorna config/selectors.yaml")
    return 0


def _cmd_discover(cfg: Config, secrets: Secrets, args) -> int:
    """Mappa le pagine della lega e scrive un report sicuro da pubblicare.

    E' il comando da lanciare quando la lettura della rosa fallisce: il report
    dice come sono fatte davvero le pagine, senza esporre HTML o screenshot.
    """
    from fantabot.lega.client import LeagueClient, load_selectors
    from fantabot.lega.discovery import competition_ids, roster_ids, to_markdown

    slug = cfg.league_slug(secrets)
    if not slug or not secrets.has_credentials:
        log.error("servono credenziali e slug della lega per il discover")
        return 1

    with LeagueClient(
        slug=slug,
        username=secrets.username or "",
        password=secrets.password or "",
        team_id=cfg.team_id(secrets),
        selectors=load_selectors(),
        headless=not args.headful,
        artifacts_dir=None,  # nessun HTML grezzo: solo il riassunto
    ) as lega:
        lega.login()
        summaries = lega.discover()

    out = cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)
    report = out / "discovery.md"
    report.write_text(to_markdown(summaries), encoding="utf-8")

    print(f"Report scritto in {report}")
    competitions = competition_ids(summaries)
    rosters = roster_ids(summaries)
    if competitions:
        print(f"Id competizione trovati: {', '.join(competitions)}")
    if rosters:
        print(f"Id rose trovati: {', '.join(rosters)}")
    for summary in summaries:
        stato = summary.error or (
            f"{len(summary.containers)} contenitori, {len(summary.links)} link"
        )
        freccia = f" -> {summary.final_url}" if summary.redirected else ""
        print(f"  {summary.name}: {stato}{freccia}")
    return 0


def _cmd_notify_test(cfg: Config, secrets: Secrets, args) -> int:
    notifier = TelegramNotifier(
        token=secrets.telegram_token,
        chat_id=secrets.telegram_chat_id,
        enabled=True,
        parse_mode=str(cfg.get("telegram.parse_mode", "HTML")),
    )
    if not notifier.configured:
        log.error("TELEGRAM_BOT_TOKEN e/o TELEGRAM_CHAT_ID mancanti")
        return 1
    ok = notifier.send(
        "\U0001f9ea <b>Fantabot</b>\nMessaggio di prova: il bot e' configurato "
        "correttamente."
    )
    print("inviato" if ok else "invio fallito")
    return 0 if ok else 1


def _dump_result(cfg: Config, result) -> None:
    """Salva il riepilogo in JSON, cosi' l'artifact CI e' ispezionabile."""
    out = cfg.output_dir
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": result.ok,
        "dry_run": result.dry_run,
        "submitted": result.submitted,
        "submit_detail": result.submit_detail,
        "error": result.error,
        "generated_at": datetime.now(UTC).isoformat(),
        "matchweek": result.matchday.matchweek if result.matchday else None,
        "deadline": result.matchday.deadline.isoformat()
        if result.matchday and result.matchday.deadline
        else None,
        "sources": [
            {"source": r.source, "ok": r.ok, "entries": len(r.entries), "error": r.error}
            for r in result.sources
        ],
    }
    if result.lineup is not None:
        payload["lineup"] = {
            "module": result.lineup.module,
            "module_scores": result.lineup.module_scores,
            "starters": [
                {
                    "name": p.player.name,
                    "team": p.player.team,
                    "role": p.role.value,
                    "status": p.verdict.status.value,
                    "score": round(p.score, 2),
                    "note": p.verdict.note,
                }
                for p in result.lineup.starters
            ],
            "bench": [p.player.name for p in result.lineup.bench],
            "decisions": result.lineup.decisions,
            "warnings": result.lineup.warnings,
        }
    (out / "result.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    sys.exit(main())
