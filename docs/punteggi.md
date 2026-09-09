# Come si calcola il punteggio di un giocatore

Ogni giocatore schierabile riceve un punteggio. La formazione poi e' meccanica:
per ogni modulo ammesso si prendono i migliori per reparto, e vince il modulo
col totale piu' alto.

Tutti i numeri qui sotto stanno in `config/config.yaml` sotto `lineup:`.
Nessuno e' scritto nel codice.

## 1. Base: cosa dicono le probabili

E' il pezzo piu' pesante, ed e' giusto che lo sia: un fuoriclasse che non
scende in campo vale zero.

| Le fonti dicono | Punti | Chiave |
|---|---:|---|
| Titolare | **100** | `scores.starter` |
| Dubbio | **45** | `scores.doubt` |
| Nessuno lo nomina | **25** | `scores.unknown` |
| In panchina | **8** | `scores.bench` |
| Infortunato o squalificato | **fuori** | — |

L'ultima riga non e' un punteggio basso: gli indisponibili sono **esclusi prima
del calcolo**. Nessuna statistica, per quanto buona, puo' rimetterli in campo.

## 2. Quanto sono d'accordo le fonti

| Voce | Formula | Intervallo | Chiave |
|---|---|---:|---|
| Probabilita' di titolarita' | `(percentuale / 100) x 25` | 0 … +25 | `weights.probabilita` |
| Accordo fra le fonti | `consenso x 15` | 0 … +15 | `weights.consenso` |

## 3. Come sta andando il giocatore

Dalla pagina `statistiche-serie-a`, una richiesta per run.

| Voce | Formula | Intervallo | Chiave |
|---|---|---:|---|
| Media voto | `(media - 6) x 12` | ~ -18 … +18 | `weights.media_voto` |
| Bonus e malus | `(fantamedia - media) x 14`, con scarto max 2,5 | -35 … +35 | `weights.bonus` |

Il **6 e' il punto neutro**: la sufficienza non sposta niente, in su o in giu'.

I bonus si ricavano come **fantamedia meno media voto**. Quella differenza *e'
gia'* il saldo di bonus e malus, calcolato dal sito col regolamento vero: gol,
assist, rigori, ammonizioni. Ricostruirla da gol e assist vorrebbe dire
indovinare quanto vale ogni voce nella nostra lega.

Per lo stesso motivo la fantamedia **non ha un peso suo**: e' la somma delle due
righe qui sopra, e pesarla di nuovo conterebbe due volte le stesse partite.

Sotto `weights.min_partite` partite giocate (2) le medie non si usano: sono
rumore, non informazione.

## 4. Chi si affronta

Dalla pagina `serie-a/classifica`, una richiesta per run. Tutto e' misurato come
**scarto dalla media del campionato**, cosi' un avversario nella norma non
sposta niente invece di spostare tutti nella stessa direzione.

Il metro cambia col ruolo, perche' cambia cosa fa male:

| Ruolo | Cosa si guarda dell'avversario | Peso | Effetto max |
|---|---|---:|---:|
| **P** | quanti gol **fa** | 10 | ± 15 |
| **D** | quanti gol **fa** | 5 | ± 7,5 |
| **C** | quanti gol **subisce** | 3 | ± 4,5 |
| **A** | quanti gol **subisce** | 4 | ± 6 |

Chiave: `weights.avversario`. In tutti i casi la regola e' la stessa —
**avversario forte, punteggio minore** — solo misurata con il metro giusto:
portiere e difensori subiscono l'attacco avversario, centrocampisti e
attaccanti trovano davanti la sua difesa. Una difesa che incassa poco vale un
punteggio minore, una che incassa tanto apre al bonus.

Il portiere ha il peso piu' alto perche' subisce l'attacco per intero e non ha
bonus con cui rifarsi.

| Voce | Formula | Intervallo | Chiave |
|---|---|---:|---|
| Forza avversario | `scarto x peso del ruolo` | vedi tabella | `weights.avversario` |
| Forma avversario | `scarto x 3 x (peso ruolo / 10)` | P ± 4,5 … C ± 1,4 | `weights.forma_avversario` |
| Forma mia squadra | `scarto x 4` | ± 6 | `weights.forma_squadra` |

La forma sono i punti per partita nelle ultime giornate (colonna "Forma" della
classifica). Anche la forma dell'avversario segue il dosaggio per ruolo:
altrimenti peserebbe uguale su tutti e cancellerebbe la distinzione fra il
portiere e gli altri.

`weights.max_scarto_avversario` (1,5) e' il tetto sullo scarto considerato: a
inizio stagione bastano tre partite per produrre medie che non significano
nulla, e senza tetto dominerebbero il calcolo.

## 5. Il resto

| Voce | Effetto | Chiave |
|---|---:|---|
| Squadra che non gioca | **-500** | `penalties.squadra_non_in_campo` |
| Ordine in rosa | `-posizione x 0,5` | `weights.ordine_rosa` |
| Spareggio fra pari merito | < 1 punto | `aggregation.tiebreakers` |

E, sul totale del modulo invece che sul singolo:

| Voce | Effetto | Chiave |
|---|---:|---|
| Ogni dubbio schierato | **-12** | `penalties.per_dubbio_schierato` |
| Difesa a 4 col modificatore | **+60** | `module_bonus.difesa_a_4_con_modificatore` |
| Difesa a 5 col modificatore | **+25** | `module_bonus.difesa_a_5_con_modificatore` |
| Posto che non riusciamo a riempire | **-1000** | — |

## 6. Puo' un forte in panchina battere uno scarso titolare?

E' la domanda che decide quanto ci fidiamo delle probabili. Ecco i conti veri,
con un attaccante da 7,4 di media e +2,6 di bonus contro un titolare da 5,5 di
media e -0,5 di bonus:

| Chi | Conto | Totale |
|---|---|---:|
| Titolare scarso | 100 + 21 (prob.) + 15 (consenso) - 6 (voto) - 7 (bonus) | **123** |
| Campione dato **dubbio** | 45 + 12 + 15 + 17 + 35 + 6 (avversario) | **127** |
| Campione dato **panchina** | 8 + 5 + 15 + 17 + 35 + 3 | **83** |

Quindi:

- **dato dubbio, il campione vince.** Se le fonti non sono sicure, decide la
  qualita': e' esattamente il caso in cui vogliamo che le statistiche pesino.
- **dato in panchina, no**, e servirebbe portare `scores.bench` da 8 a ~48 per
  ribaltarlo. Ma 48 e' piu' di `scores.doubt` (45): vorrebbe dire trattare "le
  fonti dicono che NON gioca" come "le fonti non sanno", e ogni panchinaro con
  numeri discreti comincerebbe a insidiare i titolari.

Non c'e' nessun tetto artificiale che lo impedisce
(`weights.max_totale_statistiche` e' 0, cioe' spento): il ribaltamento e'
permesso, semplicemente serve un divario che una panchina dichiarata non
colma. Se vuoi spostare la linea, quella e' la chiave da toccare — alzarla
verso 40-48 rende il caso raggiungibile, abbassarla sotto 27 riporta il comando
alle probabili in ogni situazione.

## 7. Ordine della panchina

`lineup.bench_strategy`, default `punteggio_portieri_in_fondo`: dal punteggio
piu' alto al piu' basso, **portieri sempre in coda**. Il subentro pesca il primo
della lista utile, quindi davanti va chi rende di piu'; un portiere di riserva
serve solo nel caso raro in cui il titolare non giochi, e messo in alto
ruberebbe il posto a chi puo' entrare davvero.

Alternative: `solo_punteggio` (punteggio puro, portieri compresi),
`per_ruolo_poi_punteggio` (raggruppata P, D, C, A).
