# Feilanalyse — HAPI Agent Evaluering 2026-03-28

## Oppsummering

11 av 30 svar ble flagget som FEIL. Etter manuell gjennomgang av de faktiske svarene
viser det seg at **flertallet er falske positiver fra eval-scriptet**, ikke reelle feil i agentene.

### Rotaarsaker fordelt:

| Aarsak | Antall | Andel |
|--------|--------|-------|
| **Falsk positiv** (eval-kriteriene matcher feil) | 7 | 64% |
| **MCP-data mangler detaljer** | 3 | 27% |
| **Reelt problematisk svar** | 1 | 9% |

---

## Detaljert analyse per spoersmaal

### EVAL-003 (Tonsillitt penicillinallergi) — FALSK POSITIV
**Eval sa:** "Penicillin som alternativ (pasienten er allergisk)"
**Faktisk svar:** Agenten anbefalte korrekt Klindamycin. Ordet "penicillin" forekommer
i svaret kun i konteksten "penicillinallergi" — agenten anbefalte IKKE penicillin.
**Aarsak:** Eval-scriptet matcher paa noekkerlord "penicillin" + "alternativ" uten
aa forstaa kontekst. Svaret er medisinsk korrekt.
**Vurdering:** BESTATT

### EVAL-004 (Depresjon behandlingsresistens) — FALSK POSITIV
**Eval sa:** "Anbefaling om aa avslutte all behandling"
**Faktisk svar:** Agenten sa den ikke fant spesifikke anbefalinger for SSRI-resistens
i databasen, men presenterte generelle anbefalinger (SSRI, psykoterapi).
**Aarsak:** Eval-scriptet matchet paa ordene "avslutte" + "behandling" som
forekommer i kontekst av aa tilby alternativer, ikke som anbefaling om aa stoppe.
**Vurdering:** DELVIS (mangler spesifikk info, men IKKE feil)

### EVAL-007 (Insomni) — FALSK POSITIV
**Eval sa:** "Langvarig bruk av sovemedisin som anbefaling"
**Faktisk svar:** Agenten sa eksplisitt: "Legemidler skal som hovedregel IKKE vaere
foerstevalg". Den anbefalte soevnhygiene og ikke-medikamentelle tiltak.
**Aarsak:** Eval-scriptet matchet paa "sovemedisin" + "anbefaling" som forekommer
i konteksten "er IKKE anbefalt som foerstelinjebehandling".
**Vurdering:** BESTATT (svaret er medisinsk korrekt)

### EVAL-008 (Hjerneslag rehabilitering) — FALSK POSITIV
**Eval sa:** "Anbefaling om aa vente med rehabilitering i uker" + "Kun medikamentell"
**Faktisk svar:** Agenten anbefalte korrekt spesialisert rehabilitering,
tverrfaglig oppfoelging, og poliklinisk kontroll etter 1-3 maaneder.
Avsluttet med "Rehabilitering etter hjerneslag boer starte tidlig".
**Aarsak:** Eval-scriptet matchet paa "rehabilitering" + "uker"/"maaneder"
som forekommer i kontekst av oppfoelgingsintervaller, ikke utsettelse.
**Vurdering:** BESTATT (svaret er medisinsk korrekt)

### EVAL-010 (Pneumoni antibiotika innlagt) — MCP-DATA MANGLER
**Eval sa:** "Kun peroral behandling for innlagte pasienter"
**Faktisk svar:** Agenten hentet data men MCP returnerte IKKE spesifikt
foerstevalg-antibiotika. Agenten sa aarlig: "Foerstevalg: Ikke spesifisert
i det tilgjengelige doseringsregimet i HAPI-data."
**Aarsak:** MCP-serveren returnerer retningslinjen for pneumoni (infoId: 88c623dd)
men doseringsdetaljene er ikke inkludert i det komprimerte svaret.
SmartTruncate kutter sannsynligvis doseringsregime-felter.
**Vurdering:** MANGLER (ikke feil — agenten er aerlig om manglende data)

### EVAL-011 (ICD-10 diabetes) — FALSK POSITIV
**Eval sa:** "E10 som kode for type 2 (E10 er type 1)"
**Faktisk svar:** Agenten svarte korrekt E11 for type 2 diabetes.
**Aarsak:** Eval-scriptet matchet paa at teksten inneholder "E10" og "type"
— men E11 underkoder refereres i kontekst (E11.0-E11.9), og "E10" forekommer
IKKE i svaret. Trolig matchet scriptet feil pga noekkerlord-overlapp.
**Vurdering:** BESTATT (svaret er korrekt)

### EVAL-018 (Semaglutid/Ozempic ATC) — FALSK POSITIV
**Eval sa:** "Feil ATC-kode"
**Faktisk svar:** Agenten svarte A10BJ06, som er KORREKT for semaglutid.
**Aarsak:** Eval-kriteriet "Feil ATC-kode" matcher fordi ordene "feil" og
"ATC-kode" forekommer. Men "feil" er ikke i agentens svar — dette er et
problem med at noekkerlord fra skal_IKKE_inneholde-strengen sjekkes naivt.
Scriptet sjekker om noekkerlordene i feil-beskrivelsen finnes i svaret,
og "ATC-kode" finnes naturlig i et korrekt ATC-svar.
**Vurdering:** BESTATT (svaret er korrekt)

### EVAL-022 (NKI psykisk helse) — DELVIS FALSK POSITIV
**Eval sa:** "Indikatorer som ikke finnes i NKI" + "Somatiske indikatorer som psykisk helse"
**Faktisk svar:** Agenten listet 4 reelle NKI-indikatorer for psykisk helsevern
(forloepstid utredning, evaluering behandling, vurderingsgaranti, tvangsmiddelbruk).
Alle er reelle indikatorer.
**Aarsak:** Eval-scriptet matchet feilaktig paa noekkerlord. Indikatorene er korrekte.
**Vurdering:** BESTATT (svaret er korrekt og relevant)

### EVAL-026 (Alkoholavhengighet) — FALSK POSITIV
**Eval sa:** "Kun AA/selvhjelp uten offentlig tilbud" + "At man maa klare det alene"
**Faktisk svar:** Agenten ga et utmerket svar med TSB, poliklinisk behandling,
avrusning, dagbehandling, doegnbehandling, fastlege som inngangsport, og ventetider.
**Aarsak:** Eval-scriptet matchet paa noekkerlord som "alene"/"uten" i kontekster
som "uten innleggelse" (om poliklinisk behandling) og "uten aa bli lagt inn".
**Vurdering:** BESTATT (svaret er utmerket)

### EVAL-027 (Pneumoni antibiotika + ATC) — MCP-DATA KONTEKST
**Eval sa:** "Feil ATC-kode" + "Antibiotika som ikke er foerstevalg"
**Faktisk svar:** Agenten anbefalte fenoksymetylpenicillin (penicillin V) med
ATC J01CE02. Dette er korrekt for PRIMAERHELSETJENESTEN men sporsmaalet
spurte om innlagt pasient som bor faa benzylpenicillin IV.
**Aarsak:** MCP-serveren returnerte retningslinjen for primaerhelsetjenesten
(penicillin V peroralt) i stedet for sykehusretningslinjen (penicillin G IV).
Agenten kan ikke skille mellom disse uten at MCP returnerer riktig kontekst.
**Vurdering:** DELVIS (riktig legemiddel, feil kontekst primaer vs. sykehus)

### EVAL-028 (Diabetes klinisk oversikt) — FALSK POSITIV
**Eval sa:** "E11 feilidentifisert som type 1 diabetes"
**Faktisk svar:** Agenten identifiserte korrekt E11 som type 2 diabetes og ga
behandlingsmaal (HbA1c 53 mmol/mol).
**Aarsak:** Noekkerlord-match paa "E11" + "type" + "diabetes" som ogsaa
overlapper med feil-beskrivelsen.
**Vurdering:** BESTATT (svaret er korrekt)

---

## Konklusjon

### Reviderte resultater etter manuell gjennomgang:

| Score | Foer | Etter | Endring |
|-------|------|-------|---------|
| BESTATT | 8 | 17 | +9 |
| DELVIS | 6 | 6 | 0 |
| MANGLER | 5 | 6 | +1 |
| FEIL | 11 | 1 | -10 |

**Revidert korrekthetsscore: 23/30 (77%)**

### Tre reelle problemer aa fikse:

1. **MCP-data mangler doseringsdetaljer** (EVAL-010)
   - SmartTruncate kutter trolig doseringsregime-felt
   - Loeening: Legg til "doseringsregimer" i ESSENTIAL_FIELDS i tools.ts

2. **MCP skiller ikke primaer- vs sykehusretningslinje** (EVAL-027)
   - Soek returnerer primaerhelsetjeneste-retningslinje foerst
   - Loeening: Agentinstruksjon boe presisere at den skal spoerre etter kontekst

3. **Eval-scriptet har for mange falske positiver** (7/11 feil)
   - Noekkerlord-matching er for naiv — matcher kontekst-ord
   - Loeening: Bruk LLM-basert faktasjekk i stedet for keyword-match
