"""Mappatura strutturale delle pagine della lega.

Il modulo esiste perche' quelle pagine stanno dietro login e i selettori non
sono scrivibili a tavolino. Questi test fissano due cose: che il riassunto
trovi davvero la lista dei giocatori, e che non si porti dietro roba che non
deve finire in un artifact pubblico.
"""

from __future__ import annotations

from fantabot.lega.discovery import (
    PageSummary,
    competition_ids,
    roster_ids,
    summarise,
    to_markdown,
)

SLUG = "fantasantos-2022-2023"
LINEUP_URL = f"https://leghe.fantacalcio.it/{SLUG}/view/competition/lineup"
LINEUP_FINAL = f"https://leghe.fantacalcio.it/{SLUG}/view/competition/301229/lineup"

ROLES = list("PPDDDDDDCCCCCCAAAA")


def lineup_html() -> str:
    rows = "".join(
        f'<tr class="player-row" data-player-id="{100 + i}">'
        f'<td class="role">{role}</td>'
        f'<td class="name"><a class="pl-link">Giocatore{i}</a></td>'
        f'<td class="team">Squadra{i}</td></tr>'
        for i, role in enumerate(ROLES)
    )
    return f"""
    <html><head><title>Fantasantos — Formazione</title></head><body>
      <nav>
        <a href="/{SLUG}/view/rosters/4163261">La mia rosa</a>
        <a href="/{SLUG}/view/rosters/4163262">Rosa avversario</a>
        <a href="/{SLUG}/regolamento?sid=SEGRETISSIMO">Regolamento</a>
        <a href="https://esterno.example/altro">Sito esterno</a>
        <a href="#top">Torna su</a>
        <a href="javascript:void(0)">Menu</a>
      </nav>
      <table class="lineup"><tbody class="players">{rows}</tbody></table>
    </body></html>
    """


class TestSummarise:
    def test_trova_la_lista_dei_giocatori(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_FINAL)
        top = s.containers[0]
        assert top.rows == len(ROLES)
        assert "tr.player-row" in top.selector

    def test_le_righe_di_esempio_mostrano_ruolo_nome_squadra(self):
        """E' esattamente cio' che serve per scrivere rosa.role/name/team."""
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_FINAL)
        prima = s.containers[0].samples[0]
        assert "td.role=P" in prima
        # Il nome sta dentro un <a>: la ricerca deve scendere oltre i figli diretti.
        assert "a.pl-link=Giocatore0" in prima
        assert "td.team=Squadra0" in prima

    def test_rileva_il_redirect(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_FINAL)
        assert s.redirected is True
        assert s.final_url == LINEUP_FINAL

    def test_nessun_redirect_quando_url_uguale(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_URL)
        assert s.redirected is False

    def test_titolo(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL)
        assert "Fantasantos" in s.title

    def test_censimento_classi(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL)
        assert s.class_census["tr.player-row"] == len(ROLES)

    def test_pagina_vuota_non_esplode(self):
        s = summarise("<html><body></body></html>", "vuota", LINEUP_URL)
        assert s.containers == []
        assert s.links == []


class TestLink:
    def test_solo_link_interni(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_FINAL)
        paths = [link.path for link in s.links]
        assert f"/{SLUG}/view/rosters/4163261" in paths
        assert not any("esterno.example" in p for p in paths)

    def test_scarta_ancore_e_javascript(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_FINAL)
        paths = [link.path for link in s.links]
        assert "#top" not in paths
        assert not any(p.startswith("javascript") for p in paths)

    def test_la_query_string_viene_buttata(self):
        """Puo' contenere identificativi di sessione: non deve finire nel report."""
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_FINAL)
        assert all("SEGRETISSIMO" not in link.path for link in s.links)
        assert f"/{SLUG}/regolamento" in [link.path for link in s.links]

    def test_il_report_non_contiene_la_query(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_FINAL)
        assert "SEGRETISSIMO" not in to_markdown([s])

    def test_il_report_non_contiene_html_grezzo(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_FINAL)
        report = to_markdown([s])
        assert "<table" not in report
        assert "<tbody" not in report


class TestIdentificativi:
    def test_id_competizione_dallurl_finale(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_FINAL)
        assert competition_ids([s]) == ["301229"]

    def test_id_rose_dai_link(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_FINAL)
        assert roster_ids([s]) == ["4163261", "4163262"]

    def test_nessun_id_quando_non_ce_ne_sono(self):
        s = summarise("<html><body><a href='/x/y'>y</a></body></html>", "x",
                      "https://leghe.fantacalcio.it/x/y")
        assert competition_ids([s]) == []
        assert roster_ids([s]) == []


class TestReport:
    def test_riporta_redirect_e_contenitori(self):
        s = summarise(lineup_html(), "formazione", LINEUP_URL, LINEUP_FINAL)
        report = to_markdown([s])
        assert "redirect a" in report
        assert "tr.player-row" in report
        assert "301229" in report

    def test_pagina_in_errore_finisce_nel_report(self):
        failed = PageSummary(name="regolamento", requested_url="https://x/y",
                             final_url="", error="Timeout 30000ms")
        report = to_markdown([failed])
        assert "regolamento" in report
        assert "Timeout" in report
