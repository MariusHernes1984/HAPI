import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

const BASE_URL = "https://api-qa.helsedirektoratet.no/innhold";
const SUBSCRIPTION_KEY = process.env.HAPI_SUBSCRIPTION_KEY ?? "";

async function hapiGet(
  path: string,
  params?: Record<string, string | undefined>
): Promise<unknown> {
  const url = new URL(`${BASE_URL}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") {
        url.searchParams.set(key, value);
      }
    }
  }

  const res = await fetch(url.toString(), {
    headers: {
      "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
      "Cache-Control": "no-cache",
      Accept: "application/json",
    },
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`HAPI API ${res.status}: ${res.statusText} — ${body}`);
  }

  const text = await res.text();
  if (!text) {
    return { _empty: true, _note: `HAPI returnerte tom body for ${path}` };
  }
  try {
    return JSON.parse(text);
  } catch (err) {
    throw new Error(
      `HAPI API ${path}: ugyldig JSON-respons (${(err as Error).message}). Body: ${text.slice(0, 200)}`
    );
  }
}

// Felter som er viktige for klinisk kvalitet — beholdes alltid
const ESSENTIAL_FIELDS = new Set([
  "id", "infoId", "tittel", "title", "navn", "name",
  "kortTittel", "shortTitle", "infoType",
  "status", "styrkegrad", "anbefalingstype",
  "kopiertFraInfoId", "sistFagligOppdatert", "forstPublisert",
  "koder", "kodeverdi", "kodeverk", "kode",
  "atcKode", "atcNavn", "virkestoff", "legemiddelform", "styrke",
  "sctId", "term",
  "behandlingsregimer", "doseringsregimer", "kontraindikasjoner",
  // Doserings- og legemiddeldetaljer som maa bevares
  "dose", "dosering", "enhet", "intervall", "varighet",
  "administrasjonsvei", "legemiddelnavn", "preparatnavn",
  "forstevalg", "foerstevalg", "alternativ",
  // Behandlings- og klinisk innhold som maa bevares
  "behandling", "anbefaling", "anbefalinger", "tiltak",
  "trinn", "steg", "protokoll", "regime",
  "medikament", "medikamenter", "legemiddel", "legemidler",
  "forlopstider", "forlopstid", "tidsfrist", "tidsfrister",
  "maal", "maalverdi", "nasjonaltMaal", "resultat", "verdi",
  "indikator", "indikatorverdi", "periode",
  // Kliniske tekstfelter som ofte inneholder selve anbefalingen
  "tekst", "text", "innhold", "content",
  "beskrivelse", "description", "sammendrag", "summary",
  "raadTekst", "intro", "ingress",
]);

// Klinisk-kritiske arrayfelter — ALDRI trunkeres til MAX_ARRAY_ITEMS.
// Disse inneholder dosering, behandlingsalternativer og tidsfrister
// som er kjernedata for retningslinje-agenten.
const CRITICAL_ARRAY_FIELDS = new Set([
  "behandlingsregimer", "doseringsregimer", "kontraindikasjoner",
  "forlopstider", "tidsfrister",
  "anbefalinger", "tiltak",
  "medikamenter", "legemidler",
  "trinn", "steg",
]);

// Felter som ofte er store og kan trygt forkortes
// NB: tekst, innhold, beskrivelse etc. er naa ESSENTIAL for aa bevare klinisk innhold
const VERBOSE_FIELDS = new Set([
  "html", "htmlInnhold",
]);

// Felter som trygt kan fjernes for å spare plass
// Utvidet aggressivt for å gi mer rom til klinisk innhold
const DROPPABLE_FIELDS = new Set([
  "links", "lenker", "referanser", "references",
  "vedlegg", "attachments", "metadata",
  "picoer", "pico", "picoResultater",
  "evidensgrunnlag", "kunnskapsgrunnlag",
  "sortering", "sorting", "order",
  "opphavsinformasjon", "endringshistorikk",
  "spraak", "language",
  // Utvidet: felter som tar plass uten klinisk verdi
  "relatertInnhold", "relatert", "related",
  "historikk", "versjoner", "versions",
  "publiseringsdata", "publiseringsinfo",
  "redaksjoneltInnhold", "redaksjonell",
  "kontaktinformasjon", "kontakt",
  "ikrafttredelse", "hoeringsfrist",
  "eier", "forfatter", "author", "owner",
  "godkjenner", "godkjentAv",
  "tags", "emneord", "tema", "tema2",
  "eksterneLenker", "interneLenker",
  "dokumentreferanser", "lovReferanser",
]);

const MAX_ARRAY_ITEMS = 15;      // Maks antall elementer i en vanlig array
const MAX_TEXT_LENGTH = 2000;     // Maks tegn per tekstfelt
const MAX_ESSENTIAL_LENGTH = 6000; // Økt fra 4000 — bevarer mer av dosering/anbefalinger/forløpstider
const MAX_TOTAL_LENGTH = 60000;   // Maks total JSON-lengde — 120K testet og feilet (agenter drukner i data)
const DEFAULT_SEARCH_TOP = 15;    // Maks antall resultater fra HAPI søk-API

function stripHtml(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#\d+;/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function smartTruncate(
  obj: unknown,
  depth = 0,
  textLimit = MAX_TEXT_LENGTH,
  essentialLimit = MAX_ESSENTIAL_LENGTH,
  parentKey?: string,
): unknown {
  if (depth > 12) return undefined; // Økt fra 8 — doseringsdata kan ligge dypt

  if (typeof obj === "string") {
    const clean = obj.includes("<") ? stripHtml(obj) : obj;
    // Bruk essential-grense hvis vi er inne i et klinisk-kritisk felt
    const limit = parentKey && (ESSENTIAL_FIELDS.has(parentKey) || CRITICAL_ARRAY_FIELDS.has(parentKey))
      ? essentialLimit
      : textLimit;
    return clean.length > limit
      ? clean.slice(0, limit) + "… [forkortet]"
      : clean;
  }

  if (Array.isArray(obj)) {
    // Klinisk-kritiske arrays: behold ALLE elementer (dosering, behandlingsregimer etc.)
    const isCritical = parentKey && CRITICAL_ARRAY_FIELDS.has(parentKey);
    const maxItems = isCritical ? obj.length : MAX_ARRAY_ITEMS;
    const truncated = obj.slice(0, maxItems).map((item) =>
      smartTruncate(item, depth + 1, textLimit, essentialLimit, parentKey),
    );
    if (!isCritical && obj.length > MAX_ARRAY_ITEMS) {
      truncated.push(`… og ${obj.length - MAX_ARRAY_ITEMS} flere resultater (bruk ID for detaljer)` as unknown);
    }
    return truncated;
  }

  if (obj && typeof obj === "object") {
    const result: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj)) {
      // Fjern felter som ikke tilfører klinisk verdi
      if (DROPPABLE_FIELDS.has(k)) continue;

      // Essensielle felter: behold med høyere grense og propager parentKey
      if (ESSENTIAL_FIELDS.has(k)) {
        if (typeof v === "string") {
          const clean = v.includes("<") ? stripHtml(v) : v;
          result[k] = clean.length > essentialLimit
            ? clean.slice(0, essentialLimit) + "… [forkortet]"
            : clean;
        } else {
          // Propager feltnavnet som parentKey slik at barn arver klinisk-kritisk status
          result[k] = smartTruncate(v, depth + 1, textLimit, essentialLimit, k);
        }
        continue;
      }

      // Verbose felter: strip HTML og begrens kraftig
      if (VERBOSE_FIELDS.has(k)) {
        if (typeof v === "string") {
          const clean = v.includes("<") ? stripHtml(v) : v;
          result[k] = clean.length > textLimit
            ? clean.slice(0, textLimit) + "… [forkortet]"
            : clean;
        } else {
          result[k] = smartTruncate(v, depth + 1, textLimit, essentialLimit, k);
        }
        continue;
      }

      // Alt annet: standard behandling
      result[k] = smartTruncate(v, depth + 1, textLimit, essentialLimit, k);
    }
    return result;
  }

  return obj;
}

/**
 * Normaliser ICD-10-koder som mangler punktum.
 * HAPI returnerer noen ganger "M790" i stedet for "M79.0".
 * Mønster: En bokstav + 2 siffer + 1+ siffer → sett inn punktum etter de 3 første.
 */
function normalizeIcd10Code(code: string): string {
  return code.replace(/^([A-Z]\d{2})(\d+)$/i, "$1.$2");
}

/**
 * Gå gjennom et objekt og normaliser alle ICD-10-lignende kodeverdier.
 */
function normalizeCodesInResult(obj: unknown): unknown {
  if (typeof obj === "string") return obj;
  if (Array.isArray(obj)) return obj.map(normalizeCodesInResult);
  if (obj && typeof obj === "object") {
    const result: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj)) {
      if (
        typeof v === "string" &&
        (k === "kode" || k === "kodeverdi" || k === "code") &&
        /^[A-Z]\d{3,}$/i.test(v)
      ) {
        result[k] = normalizeIcd10Code(v);
      } else {
        result[k] = normalizeCodesInResult(v);
      }
    }
    return result;
  }
  return obj;
}

/**
 * Sikkerhetsnett: Hvis JSON overskrider maks lengde, kjør smartTruncate
 * med halvert tekstgrense. Returnerer ALLTID gyldig JSON.
 */
function safeOverflowTruncate(obj: unknown, maxLen: number): unknown {
  let json = JSON.stringify(obj, null, 2);
  if (json.length <= maxLen) return obj;

  // Kjør smartTruncate en gang til med halvert tekstgrense
  const recompressed = smartTruncate(obj, 0, Math.floor(MAX_TEXT_LENGTH / 2), Math.floor(MAX_ESSENTIAL_LENGTH / 2));
  json = JSON.stringify(recompressed, null, 2);
  if (json.length <= maxLen) return recompressed;

  // Siste utvei: returner det vi har — det er fortsatt gyldig JSON
  return recompressed;
}

function formatResult(data: unknown): string {
  const compressed = smartTruncate(data);
  const normalized = normalizeCodesInResult(compressed);
  let json = JSON.stringify(normalized, null, 2);

  // Hvis over maks: prøv konservativ re-trunkering (gyldig JSON)
  if (json.length > MAX_TOTAL_LENGTH) {
    const fitted = safeOverflowTruncate(normalized, MAX_TOTAL_LENGTH);
    json = JSON.stringify(fitted, null, 2);
  }

  return json;
}


export function createServer(): McpServer {
  const server = new McpServer({
    name: "hapi-helsedirektoratet",
    version: "1.0.0",
  });

  // 1. Søk i innhold
  server.tool(
    "sok_innhold",
    "Søk i Helsedirektoratets innhold (retningslinjer, veiledere, pakkeforløp m.m.). Returnerer topp-treff basert på fritekst. Bruk hent_innhold_id for detaljer om spesifikke treff.",
    {
      queryText: z.string().describe("Søketekst / fritekst"),
      maxResults: z
        .number()
        .optional()
        .describe("Maks antall resultater (default 15, maks 30)"),
      filter: z
        .string()
        .optional()
        .describe(
          "OData-filter, f.eks. \"infoType eq 'retningslinje'\" eller \"infoType eq 'veileder'\""
        ),
      searchMode: z
        .enum(["Any", "All"])
        .optional()
        .describe("Søkemodus: Any (default) eller All"),
    },
    async ({ queryText, maxResults, filter, searchMode }) => {
      const top = Math.min(maxResults ?? DEFAULT_SEARCH_TOP, 30);
      const data = await hapiGet("/sok/infobit", {
        QueryText: queryText,
        Filter: filter,
        SearchMode: searchMode,
        QueryType: "Full",
        Top: top.toString(),
      });
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 2. Hent retningslinjer
  server.tool(
    "hent_retningslinjer",
    "Hent liste over alle nasjonale faglige retningslinjer fra Helsedirektoratet.",
    {},
    async () => {
      const data = await hapiGet("/retningslinjer");
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 3. Hent retningslinje etter ID
  server.tool(
    "hent_retningslinje",
    "Hent en spesifikk nasjonal faglig retningslinje basert på ID. Returnerer full struktur inkl. kapitler og anbefalinger.",
    {
      id: z.string().describe("Retningslinje-ID"),
      full: z
        .boolean()
        .optional()
        .describe("Om full struktur med underinnhold skal returneres"),
    },
    async ({ id, full }) => {
      const data = await hapiGet(`/retningslinjer/${encodeURIComponent(id)}`, {
        full: full?.toString(),
      });
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 4. Hent anbefalinger
  server.tool(
    "hent_anbefalinger",
    "Hent anbefalinger fra Helsedirektoratet, evt. filtrert på kodeverk og kode (f.eks. ICPC-2, ICD-10).",
    {
      kodeverk: z
        .string()
        .optional()
        .describe('Kodeverk-filter, f.eks. "ICPC-2" eller "ICD-10"'),
      kode: z
        .string()
        .optional()
        .describe('Kode innenfor kodeverket, f.eks. "A75" eller "L84"'),
      anbefalingstype: z
        .string()
        .optional()
        .describe('Anbefalingstype, f.eks. "sykmeldingslengde"'),
    },
    async ({ kodeverk, kode, anbefalingstype }) => {
      const data = await hapiGet("/anbefalinger", {
        kodeverk,
        kode,
        anbefalingstype,
      });
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 5. Hent anbefaling etter ID
  server.tool(
    "hent_anbefaling",
    "Hent en spesifikk anbefaling basert på ID.",
    {
      id: z.string().describe("Anbefalings-ID"),
      full: z.boolean().optional().describe("Om full struktur skal returneres"),
    },
    async ({ id, full }) => {
      const data = await hapiGet(`/anbefalinger/${encodeURIComponent(id)}`, {
        full: full?.toString(),
      });
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 6. Hent veiledere
  server.tool(
    "hent_veiledere",
    "Hent liste over alle nasjonale veiledere fra Helsedirektoratet.",
    {},
    async () => {
      const data = await hapiGet("/veiledere");
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 7. Hent veileder etter ID
  server.tool(
    "hent_veileder",
    "Hent en spesifikk veileder basert på ID.",
    {
      id: z.string().describe("Veileder-ID"),
      full: z.boolean().optional().describe("Om full struktur skal returneres"),
    },
    async ({ id, full }) => {
      const data = await hapiGet(`/veiledere/${encodeURIComponent(id)}`, {
        full: full?.toString(),
      });
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 8. Hent pakkeforløp
  server.tool(
    "hent_pakkeforlop",
    "Hent liste over alle pakkeforløp fra Helsedirektoratet.",
    {},
    async () => {
      const data = await hapiGet("/pakkeforl%C3%B8p");
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 9. Hent pakkeforløp etter ID
  server.tool(
    "hent_pakkeforlop_id",
    "Hent et spesifikt pakkeforløp basert på ID.",
    {
      id: z.string().describe("Pakkeforløp-ID"),
      full: z.boolean().optional().describe("Om full struktur skal returneres"),
    },
    async ({ id, full }) => {
      const data = await hapiGet(
        `/pakkeforl%C3%B8p/${encodeURIComponent(id)}`,
        { full: full?.toString() }
      );
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 10. Hent innhold (generisk)
  server.tool(
    "hent_innhold",
    "Hent innhold fra Helsedirektoratet (BETA). Kan filtreres på infotype, kodeverk, kode og målgruppe.",
    {
      infoTyper: z
        .string()
        .optional()
        .describe("Kommaseparert liste over infotyper å filtrere på"),
      kodeverk: z.string().optional().describe("Kodeverk-filter"),
      kode: z.string().optional().describe("Kode-filter"),
      maalGruppe: z
        .string()
        .optional()
        .describe("Kommaseparert liste over målgrupper"),
      skip: z.number().optional().describe("Antall resultater å hoppe over (paginering)"),
      take: z.number().optional().describe("Maks antall resultater (default 100)"),
    },
    async ({ infoTyper, kodeverk, kode, maalGruppe, skip, take }) => {
      const data = await hapiGet("/innhold", {
        infoTyper,
        kodeverk,
        kode,
        maalGruppe,
        skip: skip?.toString(),
        take: take?.toString(),
      });
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 11. Hent innhold etter ID
  server.tool(
    "hent_innhold_id",
    "Hent spesifikt innhold fra Helsedirektoratet basert på ID.",
    {
      id: z.string().describe("Innholds-ID"),
    },
    async ({ id }) => {
      const data = await hapiGet(`/innhold/${encodeURIComponent(id)}`);
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 12. Hent kvalitetsindikatorer
  server.tool(
    "hent_kvalitetsindikatorer",
    "Hent nasjonale kvalitetsindikatorer fra Helsedirektoratet. Kan filtreres på kodeverk og kode.",
    {
      kodeverk: z.string().optional().describe("Kodeverk-filter"),
      kode: z.string().optional().describe("Kode-filter"),
    },
    async ({ kodeverk, kode }) => {
      const data = await hapiGet("/kvalitetsindikatorer", { kodeverk, kode });
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 13. Hent kvalitetsindikator etter ID
  server.tool(
    "hent_kvalitetsindikator",
    "Hent en spesifikk kvalitetsindikator basert på ID.",
    {
      id: z.string().describe("Kvalitetsindikator-ID"),
      full: z.boolean().optional().describe("Om full struktur skal returneres"),
    },
    async ({ id, full }) => {
      const data = await hapiGet(
        `/kvalitetsindikatorer/${encodeURIComponent(id)}`,
        { full: full?.toString() }
      );
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  // 14. Hent endringer
  server.tool(
    "hent_endringer",
    "Hent IDer og endringstidspunkt for innhold endret fra og med et gitt tidspunkt.",
    {
      since: z
        .string()
        .describe("Tidspunkt i format YYYY-MM-DDTHH:MM:SS"),
      producerId: z
        .string()
        .optional()
        .describe("Kilde-ID, f.eks. '0006' for Enonic"),
    },
    async ({ since, producerId }) => {
      const data = await hapiGet("/GetChanges", { since, producerId });
      return { content: [{ type: "text", text: formatResult(data) }] };
    }
  );

  return server;
}
