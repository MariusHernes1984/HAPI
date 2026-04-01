# HAPI Agent Evaluering — 3x30 Konsensusrapport 2026-03-31

## Sammendrag

| Metrikk | Verdi |
|---------|-------|
| Dato | 2026-03-31 |
| Metode | LLM-faktasjekk (gpt-5.3-chat), 3 kjoringer med konsensus |
| Antall spoersmaal | 30 |
| Korrekthetsscore | **22/30 (73%)** |
| Stabilitet | **53%** (16/30 unanime) |
| Routing-korrekthet | **19/30 (63%)** |
| Snitt responstid | ~25s per spoersmaal |
| FEIL | 1 |
| HALLUSINERING | 1 |
| Tekniske feil | 0 |

### Scorefordeling (konsensus)

| Score | Antall | Andel |
|-------|--------|-------|
| BESTATT | 15 | 50% |
| DELVIS | 7 | 23% |
| MANGLER | 6 | 20% |
| FEIL | 1 | 3% |
| HALLUSINERING | 1 | 3% |

### Per kjoering — individuelle resultater

| Metrikk | Kjoering 1 | Kjoering 2 | Kjoering 3 |
|---------|-----------|-----------|-----------|
| Korrekthetsscore | 24/30 (80%) | 23/30 (77%) | 21/30 (70%) |
| BESTATT | 14 | 17 | 12 |
| DELVIS | 10 | 6 | 9 |
| MANGLER | 4 | 5 | 7 |
| FEIL | 1 | 0 | 1 |
| HALLUSINERING | 1 | 2 | 0 |
| TEKNISK_FEIL | 0 | 0 | 1 |
| Responstid | 23.4s | 26.4s | 26.7s |

---

## Resultater per kategori

| Kategori | OK | Total | BESTATT | DELVIS | MANGLER | FEIL | HALL |
|----------|----|-------|---------|--------|---------|------|------|
| retningslinje | 6 | 10 | 5 | 1 | 3 | 1 | 0 |
| kodeverk | 8 | 8 | 6 | 2 | 0 | 0 | 0 |
| statistikk | 2 | 5 | 2 | 0 | 2 | 0 | 1 |
| pasient | 2 | 3 | 1 | 1 | 1 | 0 | 0 |
| sammensatt | 3 | 3 | 0 | 3 | 0 | 0 | 0 |
| feilhaandtering | 1 | 1 | 1 | 0 | 0 | 0 | 0 |

### Hovedfunn per kategori

**Kodeverk (8/8 OK)** — Sterkeste kategori. Alle spoersmaal bestaar konsistent. ICD-10, ATC, ICPC-2, FEST-oppslag fungerer paalitelig.

**Retningslinje (6/10 OK)** — Bra paa vanlige tilstander (otitt, UVI, insomni, hjerneslag-rehab). Svakt paa sjeldnere/mer spesifikke retningslinjer (AKS sekundaerprevensjon, behandlingsresistent depresjon).

**Statistikk (2/5 OK)** — Variabelt. Fungerer for KOLS-indikatorer, men sliter med hjerneslag-trombolyse og psykisk helse-indikatorer. EVAL-020 hallusinerer data.

**Pasient (2/3 OK)** — Fungerer for KOLS og diabetes-forklaring, ustabilt for astma hos barn.

**Sammensatt (3/3 DELVIS)** — Besvarer alle, men gir aldri fullt svar. Trenger bedre syntese paa tvers av agenter.

**Feilhaandtering (1/1 BESTATT)** — Agenten haandterer ugyldig input korrekt.

---

## Kritiske funn

### 1. EVAL-020 — Hallusinering av NKI-data for hjerneslag

Agenten fabrikkerer trombolysetall og -maal i 2 av 3 kjoringer. Konkret:
- Kjoering 1: Paastaar indikatoren ikke har nasjonalt maal (feil)
- Kjoering 2: Oppgir sannsynligvis oppdiktede tall for door-to-needle
- Kjoering 3: Gir delvis svar uten maal

**Alvorlighet:** Hoey — hallusinering av medisinske kvalitetsdata er uakseptabelt.

### 2. EVAL-003 — Inkonsistent scoring paa tonsillitt-svar

Scorespennet er FEIL / BESTATT / DELVIS paa tvers av kjoringene. Problemet ser ut til aa vaere:
- Behandlingsvarighet for erytromycin (5 dager vs retningslinje)
- Prioritering av foerstevalg (klindamycin vs erytromycin)

**Vurdering:** Trolig reell usikkerhet i agentens svar, men ogsaa mulig at LLM-dommeren er inkonsistent paa dette spoersmaalet.

### 3. Tre spoersmaal mangler konsistent (unanimt)

- **EVAL-004** (Depresjon behandlingsresistens): Agenten finner ikke spesifikke anbefalinger for SSRI-resistens — unanimt MANGLER i alle 3 kjoringer.
- **EVAL-023** (Hoftebrudd NKI): Mangler data om tidsfrister — unanimt MANGLER.
- **EVAL-009** (AKS sekundaerprevensjon): Mangler dobbel platehemming, statin, betablokker — MANGLER i 2/3 kjoringer.

---

## Sammenligning med forrige eval (2026-03-29)

| Metrikk | Fase 2 (29. mars) | 3x30 (31. mars) | Endring |
|---------|-------------------|------------------|---------|
| Spoersmaal | 30 | 30 | 0 |
| Korrekthetsscore | 21/30 (70%) | 22/30 (73%) | **+3pp** |
| BESTATT | 11 | 15 | **+4** |
| DELVIS | 10 | 7 | -3 |
| MANGLER | 6 | 6 | 0 |
| FEIL | 1 | 1 | 0 |
| HALLUSINERING | 2 | 1 | **-1** |
| Routing | 21/30 (70%) | 19/30 (63%) | -7pp |
| Responstid | 25.4s | 25.5s | 0 |

### Utvikling

- Korrekthetsscore opp fra 70% til 73% — marginal forbedring
- 4 flere BESTATT-svar, faerre DELVIS — agentene gir mer komplette svar
- 1 faerre hallusinering
- Routing-korrekthet gikk ned 7pp — maa undersoekes

---

## Stabilitet (3x30 konsensus)

| Stabilitetsnivaa | Antall | Andel |
|------------------|--------|-------|
| Unanime (identisk score i alle 3 kjoringer) | 16 | 53% |
| Divergente (varierende score) | 14 | 47% |

**Ustabile spoersmaal (stoerst spenning paa tvers av kjoringer):**
- EVAL-003: FEIL / BESTATT / DELVIS
- EVAL-010: BESTATT / MANGLER / MANGLER
- EVAL-020: HALLUSINERING / HALLUSINERING / DELVIS
- EVAL-025: DELVIS / BESTATT / MANGLER

47% divergens tyder paa at agentene gir varierende svar mellom kjoringer. Dette er et omraade for forbedring — mer deterministiske prompts eller lavere temperatur kan hjelpe.

---

## Anbefalinger

### Prioritet 1 — Kritisk
1. **Fiks EVAL-020 hallusinering** — statistikk-agenten maa aldri fabrikkere NKI-tall. Legg til guard mot hallusinering naar kilde-data mangler.

### Prioritet 2 — Forbedring
2. **Utvid retningslinje-dekningen** for AKS sekundaerprevensjon (EVAL-009) og behandlingsresistent depresjon (EVAL-004).
3. **Legg til NKI-data** for hoftebrudd (EVAL-023) og psykisk helse (EVAL-022).
4. **Undersok routing-nedgang** — fra 70% til 63%.

### Prioritet 3 — Kvalitet
5. **Forbedre sammensatt-svar** — alle 3 spoersmaal er DELVIS. Bedre syntese paa tvers av agenter kan lofte disse til BESTATT.
6. **Reduser variasjon** — 47% divergens er hoyt. Vurder lavere temperatur eller mer presise instruksjoner.
7. **Stabiliser EVAL-003** — inkonsistent scoring tyder paa at agenten gir litt forskjellige svar hver gang.

---

## Rapportfiler

| Fil | Beskrivelse |
|-----|-------------|
| `rapport-20260331-2251-3x30-20260331-run1.json` | Kjoering 1/3 |
| `rapport-20260331-2315-3x30-20260331-run2.json` | Kjoering 2/3 |
| `rapport-20260331-2337-3x30-20260331-run3.json` | Kjoering 3/3 |
| `rapport-20260331-2337-3x30-20260331-combined.json` | Konsensusrapport (JSON) |
| `eval-rapport-20260331-3x30.md` | Denne rapporten |
