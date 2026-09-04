"""Normalizzazione e matching dei nomi fra fonti diverse.

Il problema concreto: la stessa persona compare come
`Bijlow` (fantacalcio.it), `Bijlow J.` (Sky), `Justin Bijlow` (articoli).
Qui costruiamo un indice per squadra e risolviamo i nomi con, in ordine:

1. alias espliciti da `config/aliases.yaml` (vincono sempre),
2. match esatto sulla forma normalizzata,
3. match sul solo cognome,
4. fuzzy matching rapidfuzz sopra una soglia configurabile.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

log = logging.getLogger(__name__)

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")
#: Iniziale puntata a fine nome, es. "Thuram M." -> gruppo "m".
_TRAILING_INITIAL = re.compile(r"\s+([a-z])\.?$")


def normalize(text: str) -> str:
    """Minuscolo, senza accenti, senza punteggiatura, spazi compattati."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    stripped = stripped.replace("'", " ").replace("’", " ")
    stripped = _PUNCT.sub(" ", stripped.lower())
    return _SPACES.sub(" ", stripped).strip()


def surname_of(name: str) -> str:
    """Il "nucleo" del nome: toglie l'iniziale puntata finale e i nomi propri.

    `Bijlow J.` -> `bijlow`; `Justin Bijlow` -> `justin bijlow` (non possiamo
    sapere quale token sia il cognome, quindi teniamo tutto e lasciamo che sia
    il fuzzy a decidere). Il caso che ci interessa davvero e' il primo.
    """
    norm = normalize(name)
    norm = _TRAILING_INITIAL.sub("", norm)
    return norm.strip()


def initial_of(name: str) -> str | None:
    """Iniziale del nome proprio, se la fonte la fornisce (`Thuram M.` -> `m`)."""
    match = _TRAILING_INITIAL.search(normalize(name))
    return match.group(1) if match else None


@dataclass
class AliasMap:
    """Alias manuali caricati da YAML."""

    players: dict[str, str] = field(default_factory=dict)
    teams: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None) -> AliasMap:
        if not path:
            return cls()
        p = Path(path)
        if not p.exists():
            log.warning("file alias non trovato: %s (procedo senza alias)", p)
            return cls()
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls(
            players=_flatten_aliases(raw.get("players") or {}),
            teams=_flatten_aliases(raw.get("teams") or {}),
        )

    def canonical_player(self, name: str) -> str:
        return self.players.get(normalize(name), normalize(name))

    def canonical_team(self, team: str) -> str:
        return self.teams.get(normalize(team), normalize(team))


def _flatten_aliases(mapping: dict) -> dict[str, str]:
    """`{"Inter": ["Internazionale"]}` -> `{"internazionale": "inter", "inter": "inter"}`."""
    out: dict[str, str] = {}
    for canonical, variants in mapping.items():
        canon_norm = normalize(str(canonical))
        out[canon_norm] = canon_norm
        for variant in variants or []:
            out[normalize(str(variant))] = canon_norm
    return out


class PlayerMatcher:
    """Risolve un nome di una fonte verso i nomi noti (tipicamente la mia rosa).

    L'indice e' costruito per squadra: se `require_same_team` e' attivo, due
    omonimi in squadre diverse non possono mai essere confusi.
    """

    def __init__(
        self,
        known: list[tuple[str, str]],
        aliases: AliasMap | None = None,
        min_score: int = 86,
        require_same_team: bool = True,
    ) -> None:
        """`known` e' una lista di `(nome, squadra)`."""
        self.aliases = aliases or AliasMap()
        self.min_score = min_score
        self.require_same_team = require_same_team
        #: (team_norm | "") -> {surname_norm: (nome originale, squadra originale)}
        self._index: dict[str, dict[str, tuple[str, str]]] = {}
        for name, team in known:
            team_key = self.aliases.canonical_team(team) if require_same_team else ""
            bucket = self._index.setdefault(team_key, {})
            bucket[self._key(name)] = (name, team)

    def _key(self, name: str) -> str:
        canonical = self.aliases.canonical_player(name)
        return surname_of(canonical)

    def match(self, name: str, team: str) -> tuple[str, str] | None:
        """Ritorna `(nome, squadra)` canonici, o `None` se nessun match affidabile."""
        team_key = self.aliases.canonical_team(team) if self.require_same_team else ""
        bucket = self._index.get(team_key)
        if bucket is None:
            if self.require_same_team:
                return None
            bucket = self._index.get("", {})
        if not bucket:
            return None

        key = self._key(name)
        if key in bucket:
            return bucket[key]

        # Sky scrive "Bijlow J.": il cognome nudo e' gia' coperto sopra, qui
        # copriamo il caso opposto (fonte con nome completo, rosa col cognome).
        for candidate_key, value in bucket.items():
            if candidate_key and (
                key.endswith(" " + candidate_key) or candidate_key.endswith(" " + key)
            ):
                return value

        best = process.extractOne(key, list(bucket.keys()), scorer=fuzz.WRatio)
        if best and best[1] >= self.min_score:
            log.debug("fuzzy match %r ~ %r (score %.0f)", name, best[0], best[1])
            return bucket[best[0]]

        return None
