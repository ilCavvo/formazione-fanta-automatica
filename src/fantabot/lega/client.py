"""Client per leghe.fantacalcio.it basato su Playwright.

Perche' Playwright e non semplici richieste HTTP: il login e' un endpoint JSON
(`POST /api/v1/User/login`, scoperto leggendo `/js/services/auth.js` del sito),
ma le pagine della lega sono renderizzate lato client e il salvataggio della
formazione passa da interazioni DOM. Un browser headless copre entrambi i casi.

Nota onesta sui limiti: le pagine rosa/formazione/regolamento stanno dietro
login, quindi i loro selettori non sono verificabili senza credenziali reali.
Per questo:

- i selettori stanno in `config/selectors.yaml` come LISTE di candidati,
  provati in ordine, modificabili senza toccare il codice;
- `fantabot inspect` salva HTML e screenshot di quelle pagine, gia' loggato,
  cosi' da correggere i selettori al primo run reale;
- ogni lettura fallita alza un errore parlante, che finisce su Telegram.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from fantabot.models import Lineup, Role, RosterPlayer

log = logging.getLogger(__name__)

DEFAULT_SELECTORS_PATH = Path("config/selectors.yaml")

_DEADLINE_TEXT = re.compile(
    r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})[^\d]{1,12}(\d{1,2})[:.](\d{2})"
)


class LeagueError(RuntimeError):
    """Errore critico lato lega: login fallito, pagina cambiata, submit rifiutato."""


@dataclass
class LeagueRules:
    """Regolamento dedotto dalla pagina della lega."""

    mode: str | None = None
    allowed_modules: list[str] = field(default_factory=list)
    modificatore_difesa: bool | None = None
    modificatore_portiere: bool | None = None
    modificatore_centrocampo: bool | None = None
    modificatore_attacco: bool | None = None
    raw_excerpt: str = ""

    def applied_to(self, cfg) -> list[str]:
        """Applica alla config quello che siamo riusciti a dedurre.

        Ritorna le righe di log delle sovrascritture, per il report Telegram.
        """
        changes: list[str] = []
        if self.mode:
            cfg.set("league.mode", self.mode)
            changes.append(f"modalita' lega: {self.mode}")
        if self.allowed_modules:
            cfg.set("league.allowed_modules", self.allowed_modules)
            changes.append(f"moduli ammessi: {', '.join(self.allowed_modules)}")
        for name in ("difesa", "portiere", "centrocampo", "attacco"):
            value = getattr(self, f"modificatore_{name}")
            if value is not None:
                cfg.set(f"league.modifiers.modificatore_{name}", value)
                changes.append(f"modificatore {name}: {'si' if value else 'no'}")
        return changes


def load_selectors(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_SELECTORS_PATH
    if not p.exists():
        raise LeagueError(f"file selettori non trovato: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


class LeagueClient:
    """Sessione autenticata verso la lega.

    Va usato come context manager: `with LeagueClient(...) as lega:`.
    """

    def __init__(
        self,
        slug: str,
        username: str,
        password: str,
        selectors: dict[str, Any] | None = None,
        headless: bool = True,
        timeout_ms: int = 30_000,
        artifacts_dir: Path | None = None,
        timezone: str = "Europe/Rome",
    ) -> None:
        self.slug = slug
        self._username = username
        self._password = password
        self.selectors = selectors or load_selectors()
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.artifacts_dir = artifacts_dir
        self.tz = ZoneInfo(timezone)

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    # -- ciclo di vita ------------------------------------------------------

    def __enter__(self) -> LeagueClient:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self) -> None:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1400, "height": 1000},
        )
        self._context.set_default_timeout(self.timeout_ms)
        self._page = self._context.new_page()

    def close(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:  # noqa: BLE001 - la chiusura non deve mai far fallire il run
                log.debug("errore chiudendo il browser", exc_info=True)
        if self._playwright is not None:
            self._playwright.stop()
        self._playwright = self._browser = self._context = self._page = None

    @property
    def page(self):
        if self._page is None:
            raise LeagueError("client non avviato: usa `with LeagueClient(...)`")
        return self._page

    # -- login --------------------------------------------------------------

    def login(self) -> None:
        """Prima via l'API JSON del sito, poi in fallback il form."""
        cfg = self.selectors["login"]

        # Serve visitare il dominio prima della POST, altrimenti il cookie di
        # sessione non viene associato al contesto del browser.
        self.page.goto(cfg["page_url"], wait_until="domcontentloaded")

        try:
            response = self._context.request.post(
                cfg["api_url"],
                data={"username": self._username, "password": self._password},
                headers={"Content-Type": "application/json"},
            )
            payload = response.json() if response.ok else {}
        except Exception as exc:  # noqa: BLE001 - proviamo comunque il form
            log.warning("login via API non riuscito (%s), provo con il form", exc)
            payload = {}

        if payload.get("success"):
            log.info("login riuscito via API")
            return

        errors = payload.get("errors") or []
        if errors:
            messages = "; ".join(str(e.get("message", e)) for e in errors)
            raise LeagueError(f"login rifiutato: {messages}")

        log.info("login via API non conclusivo, provo con il form")
        self._login_via_form(cfg)

    def _login_via_form(self, cfg: dict[str, Any]) -> None:
        self._fill_first(cfg["username_input"], self._username)
        self._fill_first(cfg["password_input"], self._password)
        self._click_first(cfg["submit_button"])
        self.page.wait_for_load_state("networkidle")

        if not self._any_visible(cfg.get("logged_in_marker", [])):
            self.save_artifacts("login-fallito")
            raise LeagueError(
                "login fallito: nessun marcatore di sessione trovato dopo il submit. "
                "Controlla credenziali o se il sito ha cambiato la pagina di login."
            )
        log.info("login riuscito via form")

    # -- lettura ------------------------------------------------------------

    def _page_url(self, key: str) -> str:
        template = self.selectors["pages"][key]
        return template.format(slug=self.slug)

    def read_roster(self) -> list[RosterPlayer]:
        """Legge la rosa dalla pagina della lega."""
        url = self._page_url("rosa")
        self.page.goto(url, wait_until="networkidle")
        cfg = self.selectors["rosa"]

        rows = self._query_all(cfg["row"])
        if not rows:
            self.save_artifacts("rosa")
            raise LeagueError(
                f"nessuna riga rosa trovata su {url}. Lancia `fantabot inspect` e "
                "aggiorna `rosa.row` in config/selectors.yaml."
            )

        roster: list[RosterPlayer] = []
        for index, row in enumerate(rows):
            name = _first_text(row, cfg["name"])
            role_raw = _first_text(row, cfg["role"])
            team = _first_text(row, cfg["team"])
            if not name or not role_raw:
                continue
            try:
                role = Role.parse(role_raw)
            except ValueError:
                log.debug("riga rosa saltata, ruolo %r non valido", role_raw)
                continue
            roster.append(
                RosterPlayer(
                    name=name,
                    team=team,
                    role=role,
                    order=index,
                    player_id=_first_attr(row, cfg.get("player_id_attr", [])),
                )
            )

        if not roster:
            self.save_artifacts("rosa")
            raise LeagueError(
                f"rosa letta ma vuota su {url}: i selettori nome/ruolo non combaciano."
            )
        log.info("rosa letta: %d giocatori", len(roster))
        return roster

    def read_rules(self) -> LeagueRules:
        """Deduce il regolamento dal testo della pagina regolamento.

        Best effort: se non capisce qualcosa lascia `None` e la config resta
        quella del file, senza bloccare il run.
        """
        url = self._page_url("regolamento")
        try:
            self.page.goto(url, wait_until="networkidle")
        except Exception as exc:  # noqa: BLE001
            log.warning("regolamento non leggibile (%s): uso i valori di config.yaml", exc)
            return LeagueRules()

        container = self._query_first(self.selectors["regolamento"]["container"])
        text = (container.inner_text() if container else self.page.inner_text("body")) or ""
        return parse_rules_text(text)

    def read_deadline(self) -> datetime | None:
        """Scadenza di schieramento letta dalla pagina formazione."""
        url = self._page_url("formazione")
        try:
            self.page.goto(url, wait_until="networkidle")
        except Exception as exc:  # noqa: BLE001
            log.warning("pagina formazione non raggiungibile (%s)", exc)
            return None

        for node in self._query_all(self.selectors["formazione"]["deadline_text"]):
            match = _DEADLINE_TEXT.search(node.inner_text() or "")
            if match:
                day, month, year, hour, minute = (int(g) for g in match.groups())
                if year < 100:
                    year += 2000
                return datetime(year, month, day, hour, minute, tzinfo=self.tz)
        return None

    # -- scrittura ----------------------------------------------------------

    def submit_lineup(self, lineup: Lineup, dry_run: bool = True) -> str:
        """Invia la formazione. Con `dry_run` si ferma un attimo prima di salvare.

        Ritorna una descrizione leggibile di cosa e' successo, che finisce nel
        messaggio Telegram.
        """
        url = self._page_url("formazione")
        self.page.goto(url, wait_until="networkidle")
        cfg = self.selectors["formazione"]

        self._select_module(cfg, lineup.module)

        placed, missing = self._place_players(lineup)
        if missing:
            self.save_artifacts("formazione-incompleta")
            raise LeagueError(
                "non sono riuscito a schierare: "
                + ", ".join(missing)
                + ". Lancia `fantabot inspect` e aggiorna formazione.player_slot."
            )

        if dry_run:
            self.save_artifacts("dry-run")
            return (
                f"DRY_RUN: formazione {lineup.module} preparata con {placed} titolari, "
                "salvataggio NON eseguito"
            )

        button = self._query_first(cfg["save_button"])
        if button is None:
            self.save_artifacts("salvataggio")
            raise LeagueError(
                "bottone di salvataggio non trovato: aggiorna formazione.save_button "
                "in config/selectors.yaml."
            )
        button.click()
        self.page.wait_for_load_state("networkidle")

        confirmed = self._any_visible(cfg.get("save_confirmation", []))
        self.save_artifacts("post-salvataggio")
        if not confirmed:
            raise LeagueError(
                "salvataggio inviato ma nessuna conferma trovata a schermo: "
                "verifica manualmente sul sito."
            )
        return f"formazione {lineup.module} salvata e confermata dal sito"

    def _select_module(self, cfg: dict[str, Any], module: str) -> None:
        select = self._query_first(cfg.get("module_select", []))
        if select is None:
            log.info("nessun select del modulo: la lega lo deduce dai giocatori schierati")
            return
        try:
            select.select_option(label=module)
        except Exception:  # noqa: BLE001 - alcune leghe usano value invece di label
            select.select_option(value=module.replace("-", ""))
        self.page.wait_for_timeout(500)

    def _place_players(self, lineup: Lineup) -> tuple[int, list[str]]:
        """Marca come titolari gli undici scelti.

        L'interfaccia della lega puo' essere a drag&drop o a click sullo slot:
        proviamo il click sul nome del giocatore, che e' il gesto comune a
        entrambe le varianti.
        """
        placed = 0
        missing: list[str] = []
        for player in lineup.starters:
            name = player.player.name
            locator = self.page.locator(
                f"text=/{re.escape(name)}/i"
            ).first
            try:
                locator.click(timeout=5_000)
                placed += 1
            except Exception:  # noqa: BLE001
                missing.append(name)
            self.page.wait_for_timeout(200)  # l'interfaccia riordina dopo ogni click
        return placed, missing

    # -- diagnostica --------------------------------------------------------

    def save_artifacts(self, label: str) -> None:
        """Salva HTML e screenshot della pagina corrente per il debug post-mortem."""
        if self.artifacts_dir is None or self._page is None:
            return
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-z0-9_-]+", "-", label.lower())
        try:
            (self.artifacts_dir / f"{safe}.html").write_text(
                self.page.content(), encoding="utf-8"
            )
            self.page.screenshot(path=str(self.artifacts_dir / f"{safe}.png"),
                                 full_page=True)
            log.info("artefatti salvati in %s (%s)", self.artifacts_dir, safe)
        except Exception:  # noqa: BLE001 - la diagnostica non deve far fallire il run
            log.debug("impossibile salvare gli artefatti", exc_info=True)

    def inspect(self) -> list[Path]:
        """Scarica le pagine che servono per tarare i selettori."""
        saved: list[Path] = []
        for key in ("rosa", "formazione", "regolamento"):
            try:
                self.page.goto(self._page_url(key), wait_until="networkidle")
                self.save_artifacts(key)
                if self.artifacts_dir is not None:
                    saved.append(self.artifacts_dir / f"{key}.html")
            except Exception as exc:  # noqa: BLE001
                log.warning("pagina %s non ispezionabile: %s", key, exc)
        return saved

    # -- helper sui selettori a lista ---------------------------------------

    def _query_first(self, candidates: list[str]):
        for selector in candidates or []:
            locator = self.page.locator(selector).first
            if locator.count() > 0:
                return locator
        return None

    def _query_all(self, candidates: list[str]) -> list:
        for selector in candidates or []:
            locator = self.page.locator(selector)
            count = locator.count()
            if count:
                return [locator.nth(i) for i in range(count)]
        return []

    def _any_visible(self, candidates: list[str]) -> bool:
        for selector in candidates or []:
            try:
                if self.page.locator(selector).first.is_visible(timeout=2_000):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _fill_first(self, candidates: list[str], value: str) -> None:
        locator = self._query_first(candidates)
        if locator is None:
            raise LeagueError(f"campo non trovato (selettori provati: {candidates})")
        locator.fill(value)

    def _click_first(self, candidates: list[str]) -> None:
        locator = self._query_first(candidates)
        if locator is None:
            raise LeagueError(f"bottone non trovato (selettori provati: {candidates})")
        locator.click()


# --------------------------------------------------------------------------
# Parsing del regolamento (funzione pura: testata senza browser)
# --------------------------------------------------------------------------

_MODULE_IN_TEXT = re.compile(r"\b([3-5]-[3-5]-[1-3])\b")

_MODIFIER_PATTERNS = {
    "difesa": re.compile(r"modificatore\s+(?:di\s+)?difesa", re.I),
    "portiere": re.compile(r"modificatore\s+(?:del\s+)?portiere", re.I),
    "centrocampo": re.compile(r"modificatore\s+(?:di\s+)?centrocampo", re.I),
    "attacco": re.compile(r"modificatore\s+(?:d[i']\s*)?attacco", re.I),
}

#: Parole che, vicino al nome del modificatore, ne indicano la disattivazione.
_NEGATIVE = re.compile(r"\b(non\s+attivo|disattivat\w*|assente|no|off)\b", re.I)


def parse_rules_text(text: str) -> LeagueRules:
    """Deduce modalita', moduli e modificatori dal testo del regolamento."""
    rules = LeagueRules(raw_excerpt=text[:1500])
    lowered = text.lower()

    if "mantra" in lowered:
        rules.mode = "mantra"
    elif "classic" in lowered:
        rules.mode = "classic"

    modules = sorted({m for m in _MODULE_IN_TEXT.findall(text)})
    valid = []
    for module in modules:
        parts = [int(p) for p in module.split("-")]
        if sum(parts) == 10:  # 10 di movimento + portiere
            valid.append(module)
    if valid:
        rules.allowed_modules = valid

    for name, pattern in _MODIFIER_PATTERNS.items():
        match = pattern.search(text)
        if match is None:
            continue
        window = text[match.end(): match.end() + 60]
        setattr(rules, f"modificatore_{name}", not bool(_NEGATIVE.search(window)))

    return rules


def _first_text(row, candidates: list[str]) -> str:
    for selector in candidates or []:
        node = row.locator(selector).first
        try:
            if node.count() > 0:
                text = (node.inner_text() or "").strip()
                if text:
                    return text
        except Exception:  # noqa: BLE001
            continue
    return ""


def _first_attr(row, candidates: list[str]) -> str | None:
    for attr in candidates or []:
        try:
            value = row.get_attribute(attr)
        except Exception:  # noqa: BLE001
            continue
        if value:
            return value
    return None
