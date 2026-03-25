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

  return res.json();
}

function truncateText(obj: unknown, maxLen = 4000): unknown {
  if (typeof obj === "string") {
    return obj.length > maxLen ? obj.slice(0, maxLen) + "…" : obj;
  }
  if (Array.isArray(obj)) {
    return obj.map((item) => truncateText(item, maxLen));
  }
  if (obj && typeof obj === "object") {
    const result: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj)) {
      result[k] = truncateText(v, maxLen);
    }
    return result;
  }
  return obj;
}

function formatResult(data: unknown): string {
  return JSON.stringify(truncateText(data), null, 2);
}

export function createServer(): McpServer {
  const server = new McpServer({
    name: "hapi-helsedirektoratet",
    version: "1.0.0",
  });

  // 1. Søk i innhold
  server.tool(
    "sok_innhold",
    "Søk i Helsedirektoratets innhold (retningslinjer, veiledere, pakkeforløp m.m.). Returnerer treff basert på fritekst.",
    {
      queryText: z.string().describe("Søketekst / fritekst"),
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
      getFullInfobits: z
        .boolean()
        .optional()
        .describe("Om full infobit-struktur skal returneres"),
    },
    async ({ queryText, filter, searchMode, getFullInfobits }) => {
      const data = await hapiGet("/sok/infobit", {
        QueryText: queryText,
        Filter: filter,
        SearchMode: searchMode,
        QueryType: "Full",
        getFullInfobits: getFullInfobits?.toString(),
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
