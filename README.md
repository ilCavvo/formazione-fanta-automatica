# formazione-fanta-automatica

Agente che, ogni settimana prima della scadenza di giornata, schiera da solo la
formazione su [leghe.fantacalcio.it](https://leghe.fantacalcio.it):

1. legge la rosa e il regolamento della lega (login con le tue credenziali);
2. scarica le probabili formazioni da piu' fonti e le aggrega a maggioranza;
3. sceglie **dinamicamente** il modulo migliore secondo regole configurabili;
4. invia la formazione al sito;
5. manda un riepilogo su Telegram.

Gira su GitHub Actions: nessun server da tenere acceso.

> **DRY_RUN e' attivo di default.** Alla prima installazione l'agente calcola la
> formazione e ti manda la notifica, ma **non** invia nulla al sito. Lo
> disattivi tu quando ti fidi (vedi [Passare in produzione](#passare-in-produzione)).

---

## Indice

- [Come funziona](#come-funziona)
- [Setup](#setup)
- [Comandi](#comandi)
- [Configurazione](#configurazione)
- [Il primo run: tarare i selettori della lega](#il-primo-run-tarare-i-selettori-della-lega)
- [Scheduling](#scheduling)
- [Passare in produzione](#passare-in-produzione)
- [Struttura del repo](#struttura-del-repo)
- [Stato delle fonti](#stato-delle-fonti)
- [Sicurezza e limiti noti](#sicurezza-e-limiti-noti)

---

## Come funziona

```
             ┌──────────────────────┐
  fonti ───▶ │ scraping (gentile)   │──┐
             └──────────────────────┘  │
             ┌──────────────────────┐  │   ┌─────────────────┐   ┌──────────────┐
  lega  ───▶ │ login + rosa + regole│──┼──▶│ aggregazione    │──▶│ modulo +     │
             └──────────────────────┘  │   │ a maggioranza   │   │ undici       │
             ┌──────────────────────┐  │   └─────────────────┘   └──────┬───────┘
  calendario▶│ deadline giornata    │──┘                                │
             └──────────────────────┘                    ┌──────────────┴───────┐
                                                         ▼                      ▼
                                                   submit al sito         Telegram
                                                  (saltato in DRY_RUN)
```

**Aggregazione a maggioranza.** Ogni fonte vota: titolare `1.0`, dubbio `0.5`,
panchina o assente `0.0`. Si fa la media pesata sulle fonti **effettivamente
disponibili**: se una fonte e' giu', le altre decidono da sole.

- media ≥ `starter_threshold` (default 0.60) → **titolare**
- media ≥ `doubt_threshold` (default 0.34) → **dubbio**
- altrimenti → **panchina**

Il caso 1-1-1 senza consenso finisce quindi in "dubbio", e a quel punto contano
i `tiebreakers` configurati (percentuale media di titolarita', presenze recenti,
fantamedia, ordine di rosa).

**Nomi diversi fra fonti.** Sky scrive `Bijlow J.`, Fantacalcio.it scrive
`Bijlow`, un articolo scrive `Justin Bijlow`. Il matching normalizza accenti e
punteggiatura, toglie l'iniziale puntata, prova il match esatto, poi il fuzzy
(`rapidfuzz`) sopra una soglia. Gli omonimi in squadre diverse non si
confondono mai perche' l'indice e' costruito per squadra. I casi che il fuzzy
non risolve si mettono a mano in `config/aliases.yaml`.

**Scelta del modulo.** In Classic i reparti sono disgiunti, quindi dato un
modulo l'undici migliore e' semplicemente "i primi N per punteggio in ogni
reparto". L'agente valuta **tutti** i moduli ammessi e sceglie il totale piu'
alto, sommando bonus e malus configurabili:

| voce | effetto |
|---|---|
| stato aggregato | titolare 100, dubbio 45, panchina 8, sconosciuto 25 |
| percentuale di titolarita' | fino a +25 |
| accordo fra le fonti | fino a +15 |
| fantamedia | fino a +8 (se disponibile) |
| squadra non in campo | −500 |
| ogni dubbio schierato | −12 sul totale del modulo |
| difesa a 4 con modificatore difesa | +60 sul totale del modulo |
| difesa a 5 con modificatore difesa | +25 |

Gli **infortunati e squalificati** sono esclusi a monte: non finiscono ne' fra i
titolari ne' in panchina, e il messaggio Telegram dice chi e' saltato e chi ha
preso il suo posto.

---

## Setup

### 1. Segreti (GitHub → Settings → Secrets and variables → Actions)

| Secret | Cos'e' |
|---|---|
| `FANTACALCIO_USERNAME` | il tuo username su fantacalcio.it |
| `FANTACALCIO_PASSWORD` | la tua password |
| `FANTACALCIO_LEAGUE_SLUG` | il pezzo di URL della lega: `https://leghe.fantacalcio.it/<slug>/` |
| `FANTACALCIO_TEAM_ID` | *(opzionale)* il numero in fondo all'URL della tua rosa. Non serve per schierare: la pagina formazione si risolve da sola sulla tua squadra |
| `TELEGRAM_BOT_TOKEN` | *(opzionale)* token del bot (te lo da' [@BotFather](https://t.me/BotFather)) |
| `TELEGRAM_CHAT_ID` | *(opzionale)* id della chat dove ricevere i messaggi |

Per il `TELEGRAM_CHAT_ID`: scrivi un messaggio al tuo bot, poi apri
`https://api.telegram.org/bot<TOKEN>/getUpdates` e leggi `message.chat.id`.

Nessuno di questi valori va mai in un file del repo.

### Telegram e' opzionale

Senza i due secret Telegram l'agente **funziona lo stesso**: legge le fonti,
calcola e schiera esattamente come prima. Cambia solo che il riepilogo non ti
arriva sul telefono: viene scritto nel log del job (lo trovi per esteso, gia'
formattato, cercando "messaggio che sarebbe stato inviato") e in
`out/result.json`.

Cosa perdi davvero: **l'avviso immediato quando qualcosa si rompe**. Se il login
fallisce o il sito cambia struttura, con Telegram lo sai subito e fai in tempo a
schierare a mano; senza, te ne accorgi solo aprendo la tab Actions. Un
rimpiazzo parziale ce l'hai gratis: GitHub ti manda un'email quando un workflow
schedulato fallisce (Settings → Notifications → Actions). Vale la pena
controllare che sia attiva se decidi di non usare il bot.

Per disattivarlo del tutto e togliere anche il warning nei log, metti
`telegram.enabled: false` in `config/config.yaml`.

### 2. Locale (opzionale, per provare)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium

cp .env.example .env      # e riempilo: .env e' in .gitignore
fantabot notify-test      # verifica il bot Telegram
fantabot probabili        # verifica le fonti (nessun login)
fantabot deadline         # quando scade la prossima giornata
```

---

## Comandi

| Comando | Cosa fa | Serve il login? |
|---|---|---|
| `fantabot run` | run completo | si |
| `fantabot run --no-dry-run` | run completo che invia davvero | si |
| `fantabot run --force` | ignora i controlli sulla deadline | si |
| `fantabot probabili` | solo scraping + aggregazione | no |
| `fantabot deadline` | deadline della prossima giornata | no |
| `fantabot discover` | mappa le pagine della lega, report sicuro da pubblicare | si |
| `fantabot inspect` | HTML e screenshot della lega, **solo in locale** | si |
| `fantabot notify-test` | messaggio di prova su Telegram | no |

Flag globali: `--config`, `--log-level DEBUG`, `--dry-run` / `--no-dry-run`,
`--headful` (mostra il browser, utile in locale).

Ogni run scrive in `out/`:

- `fantabot.log` — log completo a livello DEBUG, con i segreti oscurati;
- `result.json` — riepilogo strutturato (modulo, titolari, punteggi, decisioni);
- `raw/` — HTML delle pagine scaricate;
- `lega/` — HTML e screenshot delle pagine della lega (fondamentali se qualcosa
  va storto).

In GitHub Actions viene caricata come artifact del job **solo** la parte non
sensibile di `out/`: `out/lega/` e `out/inspect/` sono esclusi apposta, vedi
[Sicurezza](#sicurezza-e-limiti-noti).

---

## Configurazione

Tutte le regole stanno in `config/config.yaml`, commentato riga per riga. Le piu'
interessanti:

```yaml
run:
  dry_run: true                 # <- il primo flag da cambiare, quando ti fidi

league:
  autodetect_rules: true        # legge il regolamento della lega e si adatta
  allowed_modules: ["3-4-3", "3-5-2", "4-3-3", "4-4-2", "4-5-1", "5-3-2", "5-4-1"]
  modifiers:
    modificatore_difesa: false  # se true, preferisce la difesa a 4

aggregation:
  starter_threshold: 0.60
  tiebreakers: ["probabilita_media", "titolarita_recente", "fantamedia", "ordine_rosa"]

lineup:
  penalties:
    per_dubbio_schierato: 12.0  # alza per essere piu' prudente sui ballottaggi
  module_bonus:
    difesa_a_4_con_modificatore: 60.0

deadline:
  safety_margin_minutes: 20     # considera scaduta la giornata 20' prima del fischio
  skip_if_more_than_hours_before: 60
```

Altri due file:

- `config/aliases.yaml` — alias manuali per i nomi che il fuzzy non unifica;
- `config/selectors.yaml` — selettori CSS delle pagine della lega (vedi sotto).

`autodetect_rules: true` fa leggere all'agente la pagina regolamento della lega
e sovrascrivere modalita', moduli ammessi e modificatori. Se la lettura fallisce
non blocca nulla: restano i valori del file.

---

## Il primo run: tarare i selettori della lega

Le pagine della lega stanno **dietro login**, quindi i loro selettori CSS non
sono verificabili senza un account reale. Per questo stanno in
`config/selectors.yaml` come **liste di candidati** provati in ordine, e ci sono
due comandi apposta.

### `fantabot discover` — da usare per primo, anche da GitHub Actions

```bash
fantabot discover      # in locale
```

oppure, senza installare niente: **Actions → Schiera formazione → Run workflow →
`command: discover`**.

Fa login, visita le pagine note e scrive `out/discovery.md` con:

- la **mappa dei link interni** della lega (da cui si ricavano gli URL veri);
- gli **identificativi** trovati (id competizione, id delle rose);
- i **contenitori con struttura ripetuta**, cioe' i candidati per la lista dei
  giocatori, con qualche riga di esempio nella forma `classe = testo`;
- il **censimento delle classi** CSS piu' frequenti.

E' pensato per essere sicuro da allegare come artifact: niente HTML grezzo,
niente screenshot, e le query string dei link vengono rimosse perche' possono
contenere identificativi di sessione. Dal report si scrivono i selettori giusti
senza toccare il codice.

### `fantabot inspect` — solo in locale

```bash
fantabot inspect
```

Salva in `out/inspect/` l'HTML **grezzo** e uno screenshot a tutta pagina.
Molto piu' dettagliato, ma e' materiale da pagina loggata: il workflow lo
esclude apposta dagli artifact, quindi ha senso solo sulla tua macchina.

### Dove sta la rosa

Non in una pagina "rosa" separata: la legge dalla **pagina formazione**
(`/{slug}/view/competition/lineup`). Quell'URL, senza id competizione,
reindirizza da solo sulla competizione e sulla squadra dell'utente loggato —
quindi non serve conoscere il proprio `team_id`, che ogni squadra della lega ha
diverso. La sorgente resta configurabile con `rosa.page` in
`config/selectors.yaml`.

Le parti **pubbliche** (fonti delle probabili, indisponibili, calendario) sono
invece gia' verificate sul markup reale e coperte da test con fixture HTML vere.

---

## Scheduling

`.github/workflows/formazione.yml` gira piu' volte fra giovedi' e domenica (piu'
i turni infrasettimanali). Gli orari nel cron sono **UTC**.

Non serve indovinare la deadline nel cron: e' l'agente a calcolarla, dal
calendario Serie A e — dopo il login — dalla pagina formazione della lega, che
ha la precedenza. Poi decide da solo:

- mancano piu' di `skip_if_more_than_hours_before` ore → non fa nulla (le
  probabili non sono ancora affidabili);
- deadline gia' passata → non fa nulla (a meno di `--force`);
- altrimenti → schiera.

Cosi' l'ultimo run utile prima del fischio cattura gli aggiornamenti
dell'ultimo minuto, e i run inutili costano pochi secondi.

### Lanciarlo a mano

**Actions → Schiera formazione** (nella colonna di sinistra) **→ Run workflow**
(bottone in alto a destra). Il form ha tre campi:

| Campo | Cosa fa |
|---|---|
| `command` | `run` schiera; `discover` mappa le pagine della lega e scrive `out/discovery.md` nell'artifact. Usa `discover` quando `run` fallisce leggendo la rosa |
| `dry_run` | spuntato (default): calcola e notifica, non invia nulla. **Togli la spunta per schierare davvero**, senza toccare `config.yaml` |
| `force` | ignora i controlli sulla deadline: schiera anche se mancano piu' di 60h o se la deadline e' gia' passata |
| `log_level` | metti `DEBUG` quando stai debuggando: il log diventa molto piu' verboso |

Due cose da sapere:

- il bottone **Run workflow** compare solo se il workflow esiste sul branch di
  default (`main`). Se non lo vedi, e' perche' la modifica al workflow e' ancora
  su un branch non mergiato;
- senza `force`, un run lanciato a mano in un momento "sbagliato" (giornata
  lontana o gia' iniziata) finisce subito senza fare nulla, e te lo dice. Non e'
  un errore: e' la protezione che evita di schierare su probabili non ancora
  affidabili. Per una prova al volo in un giorno qualsiasi, spunta `force` e
  lascia spuntato `dry_run`.

---

## Passare in produzione

1. `fantabot inspect` e taratura di `config/selectors.yaml`.
2. Almeno una giornata intera in DRY_RUN: controlla che la notifica Telegram
   riporti una formazione che avresti schierato anche tu.
3. Controlla in `out/result.json` i `module_scores`: dicono di quanto il modulo
   scelto ha battuto gli altri.
4. Solo allora metti `run.dry_run: false` in `config/config.yaml` e committa.

Per una prova singola senza toccare la config: Actions → Run workflow →
togli la spunta a `dry_run`.

---

## Struttura del repo

```
config/
  config.yaml          regole di formazione, soglie, pesi, flag
  aliases.yaml         alias manuali dei nomi
  selectors.yaml       selettori CSS delle pagine della lega
src/fantabot/
  cli.py               comandi da riga di comando
  runner.py            orchestrazione di un run completo
  config.py            config YAML + segreti da env
  models.py            tipi condivisi
  http.py              client HTTP con rate limit, retry e cache
  names.py             normalizzazione e fuzzy matching dei nomi
  aggregate.py         voto a maggioranza fra le fonti
  lineup.py            scelta del modulo e degli undici
  deadline.py          calendario Serie A e scadenza di giornata
  notify.py            messaggi Telegram
  logging_setup.py     logging con redazione dei segreti
  sources/             un adattatore per fonte + indisponibili
  lega/                login, lettura rosa/regolamento, submit
tests/                 133 test, nessuno tocca la rete
  fixtures/            porzioni di HTML reale delle pagine pubbliche
.github/workflows/     schedulazione e CI
```

---

## Stato delle fonti

| Fonte | Stato | Cosa fornisce |
|---|---|---|
| **Fantacalcio.it** | verificata sul markup live | titolari, ruolo, **percentuale di titolarita'**, modulo, ballottaggi |
| **Sky Sport** | verificata sul markup live | titolari, riserve, **in dubbio**, squalificati, indisponibili, modulo |
| **Gazzetta** | best effort | vedi sotto |
| Indisponibili (fantacalcio.it) | verificata sul markup live | infortunati e squalificati con motivo |

**Nota onesta su Gazzetta.** Gazzetta non pubblica una pagina strutturata di
probabili formazioni: il gioco ufficiale (`magic.gazzetta.it`) e' una web app
Flutter non scrapabile e l'articolo-guida di giornata e' prosa parzialmente a
pagamento. L'adattatore cerca l'articolo della giornata e prova a estrarne il
formato classico `SQUADRA (3-5-2): Tizio; Caio, ...`; quando non lo trova si
marca "non disponibile" e **l'aggregazione prosegue sulle altre fonti**, come
previsto dal fallback. Il messaggio Telegram lo dice esplicitamente.

Se in futuro vuoi una terza fonte davvero strutturata, `sources/base.py` e
`sources/__init__.py::REGISTRY` sono fatti per aggiungerne una in un file solo.
`sources.min_sources` in config decide sotto quante fonti il run si considera
inaffidabile e si ferma senza schierare.

---

## Sicurezza e limiti noti

- **Nessun segreto nel repo.** Credenziali e token arrivano solo da variabili
  d'ambiente / GitHub Secrets. Il logger oscura i loro valori prima di scrivere,
  perche' il log finisce negli artifact della Action. Un test verifica che
  `config.yaml` non contenga parole come `password` o `token`.
- **Artifact e visibilita' della repo.** Su una repo **pubblica** gli artifact
  dei workflow sono scaricabili da chiunque. I secret non ci finiscono mai (sono
  cifrati da GitHub, mascherati nei log, e non vengono passati ai workflow
  lanciati da fork), ma `out/lega/` e `out/inspect/` conterrebbero HTML e
  screenshot delle pagine della lega **da loggato** — con possibili token
  anti-CSRF e dati del tuo account. Per questo il workflow li esclude
  esplicitamente dall'upload. Restano nell'artifact il log (con i segreti
  oscurati), `result.json` e l'HTML di pagine gia' pubbliche.
  Se ti serve il dettaglio delle pagine della lega, usa `fantabot inspect` in
  locale. Nota che log e `result.json` contengono comunque lo slug della lega e
  la tua rosa: se la cosa ti da' fastidio, **tieni la repo privata** — e' la
  scelta piu' semplice e non ha controindicazioni.
- **Scraping gentile.** Un solo User-Agent dichiarato, delay minimo fra due
  richieste allo stesso host (default 2s), retry con backoff esponenziale solo
  sugli errori transitori, cache su disco a TTL cosi' i run ravvicinati non
  ribattono sulle stesse pagine.
- **DRY_RUN di default**, disattivabile per singolo run senza toccare la config.
- **Termini di servizio.** L'automazione di un account su fantacalcio.it
  potrebbe non essere prevista dai loro ToS. E' una scelta consapevole di chi usa
  questo repo; il codice fa il possibile per comportarsi come un utente normale
  e non sovraccaricare nessuno, ma il rischio di blocco account resta.
- **Modalita' Mantra non implementata.** La lega e' Classic. I ruoli Mantra
  (Dc/Ds/E/M/C/T/W/Pc) richiedono un solutore di assegnamento diverso: se
  `autodetect_rules` legge "mantra" dal regolamento, la config viene aggiornata
  ma la logica resta quella Classic — controlla la notifica prima di fidarti.
- **Pagine della lega non verificabili senza account.** Vedi
  [Il primo run](#il-primo-run-tarare-i-selettori-della-lega).
