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
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import yaml

from fantabot.lega.discovery import PageSummary, summarise
from fantabot.models import Lineup, Role, RosterPlayer

log = logging.getLogger(__name__)

DEFAULT_SELECTORS_PATH = Path("config/selectors.yaml")

_DEADLINE_TEXT = re.compile(
    r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})[^\d]{1,12}(\d{1,2})[:.](\d{2})"
)


#: Frammenti di percorso che identificano una pagina di login.
LOGIN_PATH_MARKERS = ("/login", "/accedi", "/signin", "/sign-in")


def is_login_url(url: str) -> bool:
    """True se l'URL e' una pagina di login (di qualunque dominio del sito).

    Guardiamo solo il *percorso*: un `?next=/la/mia/pagina` non deve far
    passare per login una pagina che login non e'.
    """
    path = urlparse(url or "").path.lower().rstrip("/")
    return any(path == m or path.endswith(m) for m in LOGIN_PATH_MARKERS)


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
        team_id: str | None = None,
        selectors: dict[str, Any] | None = None,
        headless: bool = True,
        timeout_ms: int = 30_000,
        artifacts_dir: Path | None = None,
        timezone: str = "Europe/Rome",
    ) -> None:
        self.slug = slug
        self.team_id = team_id
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
        """Autentica la sessione e **verifica** che valga sulla lega.

        L'API JSON di `www.fantacalcio.it` e' la via veloce, ma da sola non
        basta: il cookie che rilascia puo' non coprire `leghe.fantacalcio.it`,
        che e' un'applicazione separata. Un login "riuscito" secondo l'API si
        traduce allora in un rimbalzo silenzioso sulla pagina di login alla
        prima navigazione — che e' esattamente il modo in cui questo e'
        andato storto la prima volta.

        Quindi: proviamo l'API, poi andiamo davvero su una pagina della lega.
        Se troviamo il muro di login, lo attraversiamo compilando il form
        (il sito stesso ci riporta indietro con il suo parametro `next`).
        """
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

        errors = payload.get("errors") or []
        if errors:
            messages = "; ".join(str(e.get("message", e)) for e in errors)
            raise LeagueError(f"login rifiutato: {messages}")

        if payload.get("success"):
            log.info("login via API accettato, verifico la sessione sulla lega")
        else:
            log.info("login via API non conclusivo, mi affido al form")

        # La verifica vera: una pagina della lega deve aprirsi senza rimbalzi.
        self._goto(self._page_url("home"))
        log.info("sessione valida su %s", self.page.url)

    def _goto(self, url: str, *, allow_login: bool = True) -> None:
        """Naviga, e se il sito rimanda al login lo attraversa e riprova.

        Tutte le navigazioni passano di qui: il rimbalzo sul login puo'
        avvenire su qualunque pagina, non solo alla prima.
        """
        self.page.goto(url, wait_until="networkidle")
        if not is_login_url(self.page.url):
            return

        if not allow_login:
            self.save_artifacts("muro-di-login")
            raise LeagueError(
                f"{url} rimanda ancora al login ({self.page.url}) anche dopo "
                "l'autenticazione. Le credenziali sono valide (l'API le accetta), "
                "quindi il problema e' la sessione su leghe.fantacalcio.it: "
                "lancia `fantabot discover`, che riporta su quali domini sono "
                "finiti i cookie."
            )

        log.info("rimbalzo sul login (%s): compilo il form", self.page.url)
        self._login_via_form(self.selectors["login"])
        self._goto(url, allow_login=False)

    def _login_via_form(self, cfg: dict[str, Any]) -> None:
        """Compila il form di login **della pagina corrente**.

        Non naviga: il muro di login puo' comparire su domini diversi
        (`www.fantacalcio.it/login`, `leghe.fantacalcio.it/login?next=...`) e
        vogliamo autenticarci proprio dove il sito ci ha portati, cosi' e' lui
        a riportarci a destinazione.
        """
        self._fill_first(cfg["username_input"], self._username)
        self._fill_first(cfg["password_input"], self._password)
        self._submit_login_form(cfg)
        try:
            self.page.wait_for_load_state("networkidle")
        except Exception:  # noqa: BLE001 - alcune pagine restano "occupate"
            log.debug("networkidle non raggiunto dopo il submit", exc_info=True)
        log.info("form di login inviato, ora su %s", self.page.url)

    def _submit_login_form(self, cfg: dict[str, Any]) -> None:
        """Invia il form di login.

        Il bottone puo' non essere selezionabile per attributo: un
        `<button>` dentro un form e' gia' di tipo submit anche senza
        `type="submit"`, quindi `button[type=submit]` non lo trova. Se nessun
        candidato matcha ripieghiamo su Invio nel campo password, che invia il
        form qualunque sia il markup del bottone.
        """
        button = self._query_first(cfg.get("submit_button", []))
        if button is not None:
            button.click()
            return

        password = self._query_first(cfg.get("password_input", []))
        if password is None:
            raise LeagueError(
                f"form di login non inviabile su {self.page.url}: ne' un bottone "
                f"fra {cfg.get('submit_button')} ne' il campo password."
            )
        log.info("nessun bottone di submit trovato: invio il form con Invio")
        password.press("Enter")

    def cookie_domains(self) -> list[tuple[str, str]]:
        """Coppie `(dominio, nome)` dei cookie di sessione. Mai i valori.

        Serve a capire *dove* vale la sessione: e' l'informazione che mancava
        per diagnosticare il rimbalzo sul login.
        """
        if self._context is None:
            return []
        try:
            cookies = self._context.cookies()
        except Exception:  # noqa: BLE001
            return []
        return sorted({(c.get("domain", ""), c.get("name", "")) for c in cookies})

    # -- lettura ------------------------------------------------------------

    def _page_url(self, key: str) -> str:
        template = self.selectors["pages"][key]
        if "{team_id}" in template and not self.team_id:
            raise LeagueError(
                f"la pagina '{key}' ha bisogno del team_id, che non e' impostato. "
                "E' il numero in fondo all'URL della tua rosa "
                "(/view/rosters/<team_id>): impostalo con FANTACALCIO_TEAM_ID "
                "oppure lancia `fantabot discover` per trovarlo."
            )
        return template.format(slug=self.slug, team_id=self.team_id or "")

    def read_roster(self) -> list[RosterPlayer]:
        """Legge la rosa dalla lega.

        Di default la prende dalla **pagina formazione**, non da una pagina
        rosa separata: e' li' che compare l'elenco dei giocatori schierabili,
        e soprattutto quella pagina si risolve da sola sulla squadra
        dell'utente loggato, senza bisogno di sapere il proprio team_id.
        La sorgente e' comunque configurabile con `rosa.page`.
        """
        cfg = self.selectors["rosa"]
        url = self._page_url(cfg.get("page", "formazione"))
        self._goto(url)

        rows = self._query_all(cfg["row"])
        if not rows:
            self.save_artifacts("rosa")
            raise LeagueError(
                f"nessuna riga rosa trovata su {url} "
                f"(pagina finale: {self.page.url}). Lancia `fantabot discover`: "
                "produce la mappa delle pagine della lega, da cui ricavare il "
                "selettore giusto per `rosa.row` in config/selectors.yaml."
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
                f"rosa letta ma vuota su {url}: ho trovato {len(rows)} righe ma i "
                "selettori nome/ruolo non combaciano. Lancia `fantabot discover` "
                "per vedere come sono fatte davvero le righe."
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
            self._goto(url)
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
            self._goto(url)
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
        self._goto(url)
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
        """Scarica le pagine che servono per tarare i selettori.

        Salva HTML **grezzo** e screenshot: sono materiale da pagina loggata,
        quindi restano in locale e non vanno pubblicati (il workflow li esclude
        dagli artifact). Per un riassunto sicuro da allegare usa `discover()`.
        """
        saved: list[Path] = []
        for key in ("home", "formazione", "rosa", "regolamento"):
            try:
                self._goto(self._page_url(key))
                self.save_artifacts(key)
                if self.artifacts_dir is not None:
                    saved.append(self.artifacts_dir / f"{key}.html")
            except Exception as exc:  # noqa: BLE001
                log.warning("pagina %s non ispezionabile: %s", key, exc)
        return saved

    def discover(self) -> list[PageSummary]:
        """Mappa la struttura delle pagine della lega, in modo pubblicabile.

        Visita le pagine note e ne produce un riassunto strutturale (link,
        classi, contenitori ripetuti). A differenza di `inspect()` non salva
        HTML grezzo ne' screenshot, quindi il risultato puo' finire negli
        artifact anche di una repo pubblica.
        """
        summaries: list[PageSummary] = []
        for key in ("home", "formazione", "rosa", "regolamento"):
            try:
                requested = self._page_url(key)
            except LeagueError as exc:
                # Tipicamente: manca il team_id. Non e' un motivo per fermarsi,
                # anzi e' proprio quello che discover deve aiutare a trovare.
                log.info("pagina %s saltata: %s", key, exc)
                continue
            try:
                self._goto(requested)
                summaries.append(
                    summarise(self.page.content(), key, requested, self.page.url)
                )
                log.info("mappata la pagina %s (-> %s)", key, self.page.url)
            except Exception as exc:  # noqa: BLE001 - una pagina rotta non blocca
                log.warning("pagina %s non mappabile: %s", key, exc)
                summaries.append(
                    PageSummary(name=key, requested_url=requested, final_url="",
                                error=str(exc)[:200])
                )
        return summaries

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
            raise LeagueError(
                f"campo non trovato su {self.page.url} (selettori provati: {candidates})"
            )
        locator.fill(value)

    def _click_first(self, candidates: list[str]) -> None:
        locator = self._query_first(candidates)
        if locator is None:
            raise LeagueError(
                f"bottone non trovato su {self.page.url} (selettori provati: {candidates})"
            )
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
