"""Scoperta della struttura delle pagine della lega.

Serve a risolvere un problema concreto: le pagine di `leghe.fantacalcio.it`
stanno dietro login, quindi non e' possibile scrivere i selettori CSS "a
tavolino". Questo modulo produce un **riassunto strutturale** della pagina —
mappa dei link, censimento delle classi, contenitori con struttura ripetuta —
da cui ricavare i selettori giusti.

Il riassunto e' pensato per essere pubblicabile come artifact: contiene classi,
conteggi e URL, piu' qualche riga di esempio troncata. Non contiene mai l'HTML
grezzo ne' screenshot, che potrebbero portarsi dietro token di sessione.

Le funzioni lavorano su stringhe HTML, non su un browser: sono quindi
testabili senza Playwright.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser, Node

#: Un contenitore e' "candidato" se ripete almeno questi figli omogenei.
MIN_REPEATED_CHILDREN = 6

#: Testo piu' lungo di cosi' viene troncato nel report.
MAX_SAMPLE_TEXT = 40

#: Id squadra dentro un URL: /view/rosters/4163261
_ROSTER_ID = re.compile(r"/view/rosters/(\d+)")
#: Id competizione dentro un URL: /view/competition/301229/lineup
_COMPETITION_ID = re.compile(r"/view/competition/(\d+)")


@dataclass(frozen=True)
class LinkInfo:
    path: str
    text: str


@dataclass
class FormInfo:
    """Un form con i suoi campi e i suoi bottoni.

    Serve al caso concreto della pagina di login: sapere *com'e' fatto* il
    bottone di invio (spesso un `<button>` senza `type`, che i selettori per
    attributo non trovano) evita un altro giro di tentativi alla cieca.
    """

    selector: str
    action: str = ""
    fields: list[str] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)


@dataclass
class ContainerInfo:
    """Un contenitore con figli tutti uguali: quasi sempre una lista/tabella."""

    selector: str
    rows: int
    #: Per le prime righe, le coppie `classe = testo` delle celle interne.
    samples: list[list[str]] = field(default_factory=list)


@dataclass
class PageSummary:
    name: str
    requested_url: str
    final_url: str
    title: str = ""
    links: list[LinkInfo] = field(default_factory=list)
    class_census: dict[str, int] = field(default_factory=dict)
    containers: list[ContainerInfo] = field(default_factory=list)
    forms: list[FormInfo] = field(default_factory=list)
    error: str | None = None

    @property
    def redirected(self) -> bool:
        return bool(self.final_url) and self.final_url != self.requested_url


def summarise(
    html: str,
    name: str,
    requested_url: str,
    final_url: str = "",
    max_links: int = 120,
    max_classes: int = 40,
    max_containers: int = 8,
) -> PageSummary:
    """Riassume una pagina in modo sicuro da pubblicare."""
    tree = HTMLParser(html)
    summary = PageSummary(
        name=name,
        requested_url=requested_url,
        final_url=final_url or requested_url,
    )

    title = tree.css_first("title")
    if title is not None:
        summary.title = title.text(strip=True)[:120]

    summary.links = _internal_links(tree, final_url or requested_url)[:max_links]
    summary.class_census = _class_census(tree, max_classes)
    summary.containers = _repeated_containers(tree)[:max_containers]
    summary.forms = _forms(tree)
    return summary


# --------------------------------------------------------------------------


def _internal_links(tree: HTMLParser, base_url: str) -> list[LinkInfo]:
    """Link interni alla lega, senza query string e senza duplicati."""
    host = urlparse(base_url).netloc
    seen: set[str] = set()
    out: list[LinkInfo] = []
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.netloc != host:
            continue
        # La query puo' contenere identificativi di sessione: la buttiamo via.
        path = parsed.path.rstrip("/") or "/"
        if path in seen:
            continue
        seen.add(path)
        out.append(LinkInfo(path=path, text=node.text(strip=True)[:60]))
    return out


def _forms(tree: HTMLParser, limit: int = 5) -> list[FormInfo]:
    """Campi e bottoni di ogni form. Nessun valore, solo la struttura."""
    out: list[FormInfo] = []
    for node in tree.css("form")[:limit]:
        info = FormInfo(
            selector=_shape_key(node),
            action=(node.attributes.get("action") or "").split("?")[0],
        )
        if node.attributes.get("id"):
            info.selector = f"form#{node.attributes['id']}"

        for field_node in node.css("input, select, textarea"):
            attrs = field_node.attributes
            parts = [str(field_node.tag)]
            for key in ("type", "name", "id"):
                if attrs.get(key):
                    parts.append(f"{key}={attrs[key]}")
            info.fields.append(" ".join(parts))

        for button in node.css("button, input[type=submit], input[type=button], a[role=button]"):
            attrs = button.attributes
            parts = [_shape_key(button)]
            # Il `type` mancante e' proprio l'informazione che interessa.
            parts.append(f"type={attrs.get('type') or '<assente>'}")
            text = button.text(strip=True)
            if text:
                parts.append(f"text={text[:30]!r}")
            info.buttons.append(" ".join(parts))

        out.append(info)
    return out


def _class_census(tree: HTMLParser, limit: int) -> dict[str, int]:
    """Quante volte compare ogni `tag.classe`. Nessun testo, solo struttura."""
    counts: dict[str, int] = {}
    for node in tree.css("*"):
        for key in _keys_of(node):
            counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return dict(ordered[:limit])


def _keys_of(node: Node) -> list[str]:
    classes = (node.attributes.get("class") or "").split()
    if not classes:
        return []
    return [f"{node.tag}.{c}" for c in classes[:2]]


def _repeated_containers(tree: HTMLParser) -> list[ContainerInfo]:
    """Contenitori i cui figli hanno tutti la stessa forma.

    Una rosa o una formazione e' sempre una struttura del genere, quindi
    elencarle in ordine di numero di righe mette quasi sempre in cima quella
    che ci interessa.
    """
    found: list[ContainerInfo] = []
    for parent in tree.css("*"):
        groups: dict[str, list[Node]] = {}
        for child in parent.iter():
            key = _shape_key(child)
            groups.setdefault(key, []).append(child)
        for key, children in groups.items():
            if len(children) < MIN_REPEATED_CHILDREN:
                continue
            found.append(
                ContainerInfo(
                    selector=f"{_shape_key(parent)} > {key}",
                    rows=len(children),
                    samples=[_sample_of(c) for c in children[:3]],
                )
            )

    # Piu' righe = piu' probabile che sia la lista che cerchiamo. A parita',
    # teniamo il contenitore piu' interno (selettore piu' lungo, quindi piu'
    # specifico), che di solito e' la lista vera e non un wrapper.
    found.sort(key=lambda c: (c.rows, len(c.selector)), reverse=True)

    deduped: list[ContainerInfo] = []
    seen: set[str] = set()
    for container in found:
        if container.selector in seen:
            continue
        seen.add(container.selector)
        deduped.append(container)
    return deduped


def _shape_key(node: Node) -> str:
    classes = (node.attributes.get("class") or "").split()
    return f"{node.tag}.{classes[0]}" if classes else str(node.tag)


def _sample_of(row: Node) -> list[str]:
    """Coppie `classe=testo` delle celle foglia di una riga."""
    out: list[str] = []
    # `css("*")` scende su tutti i discendenti: `iter()` si fermerebbe ai figli
    # diretti e perderebbe il nome del giocatore quando e' dentro un <a>.
    for node in row.css("*"):
        if node.child is not None and node.child.tag != "-text":
            continue  # non e' una foglia: il testo sta piu' in basso
        text = node.text(strip=True)
        if not text:
            continue
        key = _shape_key(node)
        out.append(f"{key}={text[:MAX_SAMPLE_TEXT]}")
        if len(out) >= 12:
            break
    return out


# --------------------------------------------------------------------------
# Estrazione degli identificativi utili
# --------------------------------------------------------------------------


def roster_ids(summaries: list[PageSummary]) -> list[str]:
    """Id squadra trovati nei link (`/view/rosters/<id>`)."""
    return _ids(summaries, _ROSTER_ID)


def competition_ids(summaries: list[PageSummary]) -> list[str]:
    """Id competizione trovati nei link o negli URL finali."""
    return _ids(summaries, _COMPETITION_ID)


def _ids(summaries: list[PageSummary], pattern: re.Pattern[str]) -> list[str]:
    found: list[str] = []
    for summary in summaries:
        for candidate in (summary.final_url, summary.requested_url):
            match = pattern.search(candidate or "")
            if match and match.group(1) not in found:
                found.append(match.group(1))
        for link in summary.links:
            match = pattern.search(link.path)
            if match and match.group(1) not in found:
                found.append(match.group(1))
    return found


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def _session_section(
    cookies: list[tuple[str, str]], summaries: list[PageSummary]
) -> list[str]:
    """Su quali domini vale la sessione, e quali pagine rimbalzano al login.

    E' la sezione diagnostica: `www.fantacalcio.it` e `leghe.fantacalcio.it`
    sono applicazioni distinte, quindi un login accettato dalla prima non
    implica una sessione valida sulla seconda. Riportiamo solo dominio e nome
    dei cookie: mai i valori, che sono a tutti gli effetti credenziali.
    """
    lines = ["## Sessione", ""]

    by_domain: dict[str, list[str]] = {}
    for domain, name in cookies:
        by_domain.setdefault(domain, []).append(name)
    if by_domain:
        lines.append("Cookie presenti (solo dominio e nome, mai i valori):")
        lines.append("")
        for domain in sorted(by_domain):
            lines.append(f"- `{domain}`: {', '.join(sorted(by_domain[domain]))}")
    else:
        lines.append("Nessun cookie nel contesto: la sessione non e' partita.")
    lines.append("")

    walled = [s for s in summaries if _looks_like_login(s.final_url)]
    if walled:
        lines.append("**Pagine rimbalzate sul login:**")
        lines.append("")
        for summary in walled:
            lines.append(f"- {summary.name} -> `{summary.final_url}`")
        lines.append("")

    return lines


def _looks_like_login(url: str) -> bool:
    from urllib.parse import urlparse as _urlparse

    path = _urlparse(url or "").path.lower().rstrip("/")
    return any(path == m or path.endswith(m) for m in ("/login", "/accedi", "/signin"))


def to_markdown(
    summaries: list[PageSummary],
    cookies: list[tuple[str, str]] | None = None,
    login_error: str | None = None,
) -> str:
    """Report leggibile, pensato per essere allegato come artifact."""
    lines = [
        "# fantabot — mappa delle pagine della lega",
        "",
        "Riassunto strutturale delle pagine dietro login: link, classi CSS e",
        "contenitori con struttura ripetuta. Serve a scrivere i selettori in",
        "`config/selectors.yaml`. Non contiene HTML grezzo ne' screenshot.",
        "",
    ]

    if login_error:
        lines.extend([
            "> **Il login e' fallito.** Quello che segue e' quanto si vede",
            "> comunque: tipicamente la pagina di login stessa, la cui sezione",
            "> *Form* dice come sono fatti i campi e il bottone di invio.",
            "",
            f"```\n{login_error}\n```",
            "",
        ])

    if cookies is not None:
        lines.extend(_session_section(cookies, summaries))

    competitions = competition_ids(summaries)
    rosters = roster_ids(summaries)
    if competitions or rosters:
        lines.append("## Identificativi trovati")
        lines.append("")
        if competitions:
            lines.append(f"- competizione: {', '.join(competitions)}")
        if rosters:
            lines.append(f"- rose: {', '.join(rosters)}")
        lines.append("")

    for summary in summaries:
        lines.append(f"## {summary.name}")
        lines.append("")
        lines.append(f"- richiesto: `{summary.requested_url}`")
        if summary.redirected:
            lines.append(f"- **redirect a**: `{summary.final_url}`")
        if summary.title:
            lines.append(f"- titolo: {summary.title}")
        if summary.error:
            lines.append(f"- **errore**: {summary.error}")
            lines.append("")
            continue
        lines.append("")

        if summary.containers:
            lines.append("### Contenitori ripetuti (candidati per la rosa)")
            lines.append("")
            for container in summary.containers:
                lines.append(f"- `{container.selector}` — {container.rows} righe")
                for sample in container.samples:
                    lines.append(f"  - {' | '.join(sample)}" if sample else "  - (vuota)")
            lines.append("")

        if summary.forms:
            lines.append("### Form")
            lines.append("")
            for form in summary.forms:
                azione = f" (action `{form.action}`)" if form.action else ""
                lines.append(f"- `{form.selector}`{azione}")
                for campo in form.fields:
                    lines.append(f"  - campo: `{campo}`")
                for bottone in form.buttons:
                    lines.append(f"  - bottone: `{bottone}`")
            lines.append("")

        if summary.class_census:
            lines.append("### Classi piu' frequenti")
            lines.append("")
            for key, count in summary.class_census.items():
                lines.append(f"- `{key}` x{count}")
            lines.append("")

        if summary.links:
            lines.append("### Link interni")
            lines.append("")
            for link in summary.links:
                label = f" — {link.text}" if link.text else ""
                lines.append(f"- `{link.path}`{label}")
            lines.append("")

    return "\n".join(lines)
