"""Statistiche di Serie A: com'e' andata la squadra e com'e' andato il giocatore.

Servono a due cose che le probabili formazioni non sanno dire:

- **chi si incontra**: un portiere che affronta l'attacco piu' prolifico del
  campionato vale meno di uno che affronta il piu' sterile, a parita' di
  titolarita';
- **come sta andando**: media voto, bonus e malus accumulati finora.

Due sole pagine, sullo stesso host che gia' interroghiamo per le probabili:

    /serie-a/classifica       gol fatti e subiti, partite, forma recente
    /statistiche-serie-a      per ogni giocatore: PV, MV, FM, gol, assist, ...

Non c'e' un'API: `statistiche-serie-a` e' l'unica fonte strutturata, e le
chiavi delle colonne (`data-col-key`) sono quelle che il sito stesso usa per
ordinare la tabella — piu' stabili delle posizioni delle celle.

Una statistica che manca non e' un errore: e' semplicemente un contributo che
non entra nel punteggio. Il run non deve mai fermarsi per questo.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

log = logging.getLogger(__name__)

CLASSIFICA_URL = "https://www.fantacalcio.it/serie-a/classifica"
STATISTICHE_URL = "https://www.fantacalcio.it/statistiche-serie-a"

#: Punti per esito nella colonna "Forma" della classifica.
_FORM_POINTS = {"W": 3.0, "D": 1.0, "L": 0.0}

_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


def _number(text: str | None) -> float | None:
    """`7,5` -> 7.5. Le celle vuote valgono `None`, non zero.

    La differenza conta: un giocatore senza voti non e' un giocatore da zero.
    """
    match = _NUMBER.search(text or "")
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def _integer(text: str | None) -> int:
    value = _number(text)
    return int(value) if value is not None else 0


@dataclass(frozen=True)
class TeamStats:
    """Rendimento di una squadra finora."""

    name: str
    played: int = 0
    goals_for: int = 0
    goals_against: int = 0
    #: Esiti recenti dal piu' vecchio al piu' nuovo: "W", "D", "L".
    form: tuple[str, ...] = ()

    @property
    def attack(self) -> float | None:
        """Gol fatti per partita: quanto fa male questa squadra."""
        return self.goals_for / self.played if self.played else None

    @property
    def defence(self) -> float | None:
        """Gol subiti per partita: quanto e' facile segnarle."""
        return self.goals_against / self.played if self.played else None

    @property
    def form_points(self) -> float | None:
        """Punti per partita nelle ultime, 0-3."""
        if not self.form:
            return None
        return sum(_FORM_POINTS.get(e, 0.0) for e in self.form) / len(self.form)


@dataclass(frozen=True)
class PlayerStats:
    """Rendimento di un giocatore finora, dalla pagina statistiche."""

    name: str
    team: str
    played: int = 0
    media_voto: float | None = None
    fantamedia: float | None = None
    goals: int = 0
    goals_conceded: int = 0
    assists: int = 0
    penalties_saved: int = 0
    yellow_cards: int = 0
    red_cards: int = 0

    @property
    def bonus_per_match(self) -> float | None:
        """Bonus/malus medi a partita, cioe' fantamedia meno media voto.

        E' la via onesta per misurarli: la differenza fra i due numeri **e'**
        gia' il saldo di bonus e malus, calcolato dal sito con il regolamento
        vero. Ricostruirlo da gol e assist vorrebbe dire indovinare quanto vale
        ogni voce nella nostra lega.
        """
        if self.media_voto is None or self.fantamedia is None:
            return None
        return self.fantamedia - self.media_voto


@dataclass
class StatsBook:
    """Le statistiche di giornata, pronte da consultare."""

    teams: dict[str, TeamStats] = field(default_factory=dict)
    players: list[PlayerStats] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.teams or self.players)

    # -- medie di campionato, per centrare i confronti ----------------------

    @property
    def average_attack(self) -> float | None:
        return _average(t.attack for t in self.teams.values())

    @property
    def average_defence(self) -> float | None:
        return _average(t.defence for t in self.teams.values())

    @property
    def average_form(self) -> float | None:
        return _average(t.form_points for t in self.teams.values())

    def team(self, name: str | None) -> TeamStats | None:
        if not name:
            return None
        return self.teams.get(_key(name))


def _average(values) -> float | None:
    numeri = [v for v in values if v is not None]
    return sum(numeri) / len(numeri) if numeri else None


def _key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


# --------------------------------------------------------------------------
# Lettura delle pagine
# --------------------------------------------------------------------------


def parse_classifica(html: str) -> dict[str, TeamStats]:
    """Gol fatti/subiti e forma recente, dalla classifica di Serie A."""
    tree = HTMLParser(html)
    squadre: dict[str, TeamStats] = {}

    for row in tree.css("tr[data-name]"):
        nome = (row.attributes.get("data-name") or "").strip()
        if not nome:
            continue
        # In fondo alla pagina un widget ripete le stesse squadre con le sole
        # colonne posizione e punti. Senza questo controllo quelle righe
        # sovrascrivono le buone e ogni squadra risulta a zero gol.
        if row.css_first("td.goalsscored") is None:
            continue
        forma = tuple(
            (span.attributes.get("data-value") or "").upper()
            for span in row.css("td.form span.value")
            if span.attributes.get("data-value")
        )
        squadre[_key(nome)] = TeamStats(
            name=nome,
            played=_integer(_cell(row, "played")),
            goals_for=_integer(_cell(row, "goalsscored")),
            goals_against=_integer(_cell(row, "goalsconceded")),
            form=forma,
        )

    return squadre


def _cell(row, css_class: str) -> str | None:
    node = row.css_first(f"td.{css_class}")
    return node.text(strip=True) if node is not None else None


def parse_statistiche(html: str) -> list[PlayerStats]:
    """Statistiche per giocatore, lette dalle chiavi di colonna del sito."""
    tree = HTMLParser(html)
    giocatori: list[PlayerStats] = []

    for row in tree.css("tr.player-row"):
        nome_node = row.css_first("th.player-name span") or row.css_first("th.player-name")
        if nome_node is None:
            continue
        nome = nome_node.text(strip=True)
        if not nome:
            continue

        col = {
            (td.attributes.get("data-col-key") or ""): td.text(strip=True)
            for td in row.css("td[data-col-key]")
        }
        giocatori.append(
            PlayerStats(
                name=nome,
                team=col.get("sq", "").strip(),
                played=_integer(col.get("pg")),
                media_voto=_number(col.get("mv")),
                # Il sito chiama la fantamedia `mfv`.
                fantamedia=_number(col.get("mfv")),
                goals=_integer(col.get("gol")),
                goals_conceded=_integer(col.get("gs")),
                assists=_integer(col.get("ass")),
                penalties_saved=_integer(col.get("rp")),
                yellow_cards=_integer(col.get("amm")),
                red_cards=_integer(col.get("esp")),
            )
        )

    return giocatori


# --------------------------------------------------------------------------
# Dalla pagina alla mia rosa
# --------------------------------------------------------------------------


@dataclass
class FormGuide:
    """Quello che sappiamo su un giocatore oltre alle probabili.

    Tiene insieme tre cose che vengono da posti diversi — le statistiche, il
    calendario e la mia rosa — cosi' il calcolo del punteggio puo' chiedere
    "chi affronta" e "come sta andando" senza sapere nulla di come si appaiano
    i nomi.
    """

    book: StatsBook
    #: squadra normalizzata -> avversario di giornata (nome come da calendario).
    opponents: dict[str, str] = field(default_factory=dict)
    #: nome del giocatore in rosa -> sue statistiche.
    by_player: dict[str, PlayerStats] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.book)

    def stats_for(self, player_name: str) -> PlayerStats | None:
        return self.by_player.get(player_name)

    def team_of(self, team: str | None) -> TeamStats | None:
        return self.book.team(team)

    def opponent_name(self, team: str | None) -> str | None:
        return self.opponents.get(_key(team or ""))

    def opponent_of(self, team: str | None) -> TeamStats | None:
        return self.book.team(self.opponent_name(team))


def build_form_guide(book: StatsBook, matchday=None, roster=None,
                     aliases=None) -> FormGuide:
    """Collega statistiche, calendario e rosa."""
    guide = FormGuide(book=book)

    for match in getattr(matchday, "matches", None) or ():
        guide.opponents[_key(match.home)] = match.away
        guide.opponents[_key(match.away)] = match.home

    if roster:
        guide.by_player = _match_players(book, roster, aliases)

    return guide


def _match_players(book: StatsBook, roster, aliases) -> dict[str, PlayerStats]:
    """Appaia le righe della pagina statistiche ai giocatori della mia rosa.

    La pagina scrive la squadra come sigla (`ROM`) e il giocatore col solo
    cognome: la sigla si scioglie sui nomi veri della classifica, il nome lo
    risolve lo stesso matcher sfocato che usiamo per le probabili.
    """
    from fantabot.names import PlayerMatcher, resolve_team

    if not book.players:
        return {}

    noti = [(p.name, p.team) for p in roster]
    matcher = PlayerMatcher(noti, aliases=aliases)
    squadre = {t.name for t in book.teams.values()}

    trovati: dict[str, PlayerStats] = {}
    for stat in book.players:
        squadra = resolve_team(stat.team, squadre, aliases) if squadre else stat.team
        esito = matcher.match(stat.name, squadra)
        if esito is None:
            continue
        nome_in_rosa = esito[0]
        # Un cognome puo' tornare due volte (omonimi, o un match debole):
        # teniamo chi ha giocato di piu', che e' quasi sempre quello giusto.
        precedente = trovati.get(nome_in_rosa)
        if precedente is None or stat.played > precedente.played:
            trovati[nome_in_rosa] = stat

    mancanti = len(roster) - len(trovati)
    if mancanti > 0:
        log.info("statistiche: %d giocatori della rosa senza riscontro", mancanti)
    return trovati


def fetch_stats(client, *, classifica_url: str = CLASSIFICA_URL,
                statistiche_url: str = STATISTICHE_URL,
                save_raw=None) -> StatsBook:
    """Scarica entrambe le pagine. Un guasto si annota e si tira avanti."""
    book = StatsBook()

    for etichetta, url, leggi in (
        ("classifica", classifica_url, parse_classifica),
        ("statistiche", statistiche_url, parse_statistiche),
    ):
        try:
            risposta = client.get(url)
            if save_raw is not None:
                save_raw(f"{etichetta}.html", risposta.text)
            letto = leggi(risposta.text)
        except Exception as exc:  # noqa: BLE001 - le statistiche non bloccano il run
            log.warning("statistiche: %s non disponibile (%s)", etichetta, exc)
            book.errors.append(f"{etichetta}: {exc}")
            continue

        if not letto:
            log.warning("statistiche: %s letta ma vuota (%s)", etichetta, url)
            book.errors.append(f"{etichetta}: nessuna riga letta")
            continue

        if etichetta == "classifica":
            book.teams = letto
        else:
            book.players = letto

    log.info("statistiche: %d squadre, %d giocatori", len(book.teams), len(book.players))
    return book
