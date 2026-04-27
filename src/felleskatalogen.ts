/**
 * MCP-tools for Felleskatalogen-doseringsoppslag.
 *
 * KRITISK: Disse verktøyene returnerer VERBATIM tekst fra Felleskatalogen.
 * Agenten som bruker dem må aldri omformulere svaret. Synthesis-laget i
 * orkestratoren omgås for denne agenten — verbatim sitat går rett til bruker.
 *
 * Lisens: innholdet er Felleskatalogens åndsverk. Demo-bruk forutsetter at
 * lisensavtale er på plass før produksjon.
 *
 * Datakilde: SQLite-fil bygget av felleskatalogen-scraper/scrape.py.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import Database from "better-sqlite3";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function resolveDbPath(): string {
  const override = process.env.FELLESKATALOGEN_DB_PATH;
  if (override && existsSync(override)) return override;
  const candidates = [
    resolve(__dirname, "../felleskatalogen-scraper/data/felleskatalogen.db"),
    resolve(__dirname, "../../felleskatalogen-scraper/data/felleskatalogen.db"),
    resolve(__dirname, "./felleskatalogen.db"),
    resolve(process.cwd(), "felleskatalogen-scraper/data/felleskatalogen.db"),
    resolve(process.cwd(), "felleskatalogen.db"),
  ];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return candidates[0];
}

let db: Database.Database | null = null;
function getDb(): Database.Database {
  if (db) return db;
  const path = resolveDbPath();
  if (!existsSync(path)) {
    throw new Error(
      `Felleskatalogen-DB ikke funnet (${path}). Kjør 'python felleskatalogen-scraper/scrape.py' først.`
    );
  }
  db = new Database(path, { readonly: true, fileMustExist: true });
  db.pragma("query_only = true");
  return db;
}

const ALLE_SEKSJONER = [
  "Indikasjoner",
  "Dosering",
  "Administrering",
  "Kontraindikasjoner",
  "Forsiktighetsregler",
  "Interaksjoner",
  "Graviditet, amming og fertilitet",
  "Bivirkninger",
  "Overdosering",
  "Egenskaper",
] as const;

type Seksjon = (typeof ALLE_SEKSJONER)[number];

interface PreparatRow {
  id: number;
  navn: string;
  produsent: string | null;
  atc: string | null;
  virkestoff: string | null;
  url: string;
  tags: string | null;
  scrape_dato: string;
  sist_endret_kilde: string | null;
}

interface SeksjonRow {
  seksjon: string;
  innhold_tekst: string;
  rekkefolge: number;
}

function ftsQuery(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return '""';
  const terms = trimmed
    .split(/\s+/)
    .filter((t) => t.length > 0 && !/^[-+()"*]+$/.test(t))
    .map((t) => `"${t.replace(/"/g, "")}"`);
  return terms.length ? terms.join(" AND ") : '""';
}

export function registerFelleskatalogenTools(server: McpServer): void {
  // 1. Søk preparat
  server.tool(
    "sok_felleskatalogen",
    "Søk i Felleskatalogen-databasen etter preparat (norsk navn, virkestoff eller ATC-kode). " +
      "Returnerer treff-liste med ID som kan brukes i hent_felleskatalogen_dosering. " +
      "VIKTIG: Innholdet er VERBATIM fra Felleskatalogen og må ikke omformuleres.",
    {
      query: z
        .string()
        .describe("Søketekst — preparatnavn (Paracet), virkestoff (paracetamol) eller ATC (N02BE01)"),
      maxResults: z.number().optional().describe("Maks antall treff (default 8, maks 20)"),
    },
    async ({ query, maxResults }) => {
      const database = getDb();
      const top = Math.min(maxResults ?? 8, 20);
      const fts = ftsQuery(query);

      let rows: PreparatRow[];
      try {
        rows = database
          .prepare(
            `SELECT p.* FROM preparater_fts f
             JOIN preparater p ON p.id = f.rowid
             WHERE preparater_fts MATCH ?
             ORDER BY rank
             LIMIT ?`
          )
          .all(fts, top) as PreparatRow[];
      } catch {
        rows = [];
      }
      // Fallback til LIKE hvis FTS ga 0 treff
      if (rows.length === 0) {
        const likeQ = `%${query}%`;
        rows = database
          .prepare(
            `SELECT * FROM preparater
             WHERE navn LIKE ? OR virkestoff LIKE ? OR atc LIKE ?
             LIMIT ?`
          )
          .all(likeQ, likeQ, likeQ, top) as PreparatRow[];
      }

      const result = {
        treff: rows.length,
        resultater: rows.map((r) => ({
          id: r.id,
          navn: r.navn,
          produsent: r.produsent,
          virkestoff: r.virkestoff,
          atc: r.atc,
          url: r.url,
          tags: r.tags ? JSON.parse(r.tags) : [],
          scrape_dato: r.scrape_dato,
        })),
        kilde: "Felleskatalogen.no",
        lisens_status: "POC — kommersiell avtale ikke etablert. Innhold er Felleskatalogens åndsverk.",
      };
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 2. Hent dosering (eller andre seksjoner) verbatim
  server.tool(
    "hent_felleskatalogen_dosering",
    "Hent VERBATIM tekst fra én eller flere seksjoner av Felleskatalogen-preparatomtalen. " +
      "Default returnerer Dosering. Kan også hente Indikasjoner, Kontraindikasjoner, " +
      "Forsiktighetsregler, Interaksjoner, Graviditet/amming, Bivirkninger, Overdosering. " +
      "AGENT-INSTRUKS: Sitér ordrett. Aldri omformulér eller utled fra teksten. " +
      "Oppgi alltid kildelink og scrape-dato i svaret.",
    {
      preparatId: z.number().describe("Preparat-ID fra sok_felleskatalogen"),
      seksjoner: z
        .array(z.enum(ALLE_SEKSJONER))
        .optional()
        .describe("Hvilke seksjoner. Default ['Dosering']."),
    },
    async ({ preparatId, seksjoner }) => {
      const database = getDb();
      const wantedSections: Seksjon[] = (seksjoner ?? ["Dosering"]) as Seksjon[];

      const preparat = database
        .prepare("SELECT * FROM preparater WHERE id = ?")
        .get(preparatId) as PreparatRow | undefined;

      if (!preparat) {
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  feil: `Preparat ${preparatId} finnes ikke i Felleskatalogen-databasen. Bruk sok_felleskatalogen først.`,
                },
                null,
                2
              ),
            },
          ],
        };
      }

      const placeholders = wantedSections.map(() => "?").join(",");
      const sections = database
        .prepare(
          `SELECT seksjon, innhold_tekst, rekkefolge FROM seksjoner
           WHERE preparat_id = ? AND seksjon IN (${placeholders})
           ORDER BY rekkefolge`
        )
        .all(preparatId, ...wantedSections) as SeksjonRow[];

      const seksjonerOutput: Record<string, string> = {};
      const manglendeSeksjoner: string[] = [];
      // Trunkér svært lange seksjoner (typisk Dosering for Klexane/OxyContin)
      // for å unngå at agentens output-token-grense kutter kildelink/disclaimer.
      const MAX_LEN = 2500;
      for (const s of wantedSections) {
        const found = sections.find((x) => x.seksjon === s);
        if (found) {
          let txt = found.innhold_tekst;
          if (txt.length > MAX_LEN) {
            txt =
              txt.slice(0, MAX_LEN - 100) +
              `... [forkortet — se fullstendig ${s.toLowerCase()} i preparatomtalen på felleskatalogen.no]`;
          }
          seksjonerOutput[s] = txt;
        } else {
          manglendeSeksjoner.push(s);
        }
      }

      const result = {
        preparat: {
          id: preparat.id,
          navn: preparat.navn,
          produsent: preparat.produsent,
          virkestoff: preparat.virkestoff,
          atc: preparat.atc,
        },
        seksjoner_verbatim: seksjonerOutput,
        manglende_seksjoner:
          manglendeSeksjoner.length > 0 ? manglendeSeksjoner : undefined,
        kilde: {
          navn: "Felleskatalogen.no",
          url: preparat.url,
          scrape_dato: preparat.scrape_dato,
          sist_endret_kilde: preparat.sist_endret_kilde,
          lisens_status:
            "POC-data. Verifiser alltid mot fullstendig preparatomtale ved klinisk forskrivning.",
        },
        instruks_til_agent:
          "Returner teksten i 'seksjoner_verbatim' ORDRETT til bruker. Ikke omformulér, ikke utled, ikke kombinér med annen kunnskap. Avslutt med kildelenke.",
      };
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    }
  );

  // 3. Liste alle preparater (for diagnostikk og UI)
  server.tool(
    "list_felleskatalogen_preparater",
    "Liste alle preparater som finnes i Felleskatalogen-demo-databasen.",
    {},
    async () => {
      const database = getDb();
      const rows = database
        .prepare(
          "SELECT id, navn, produsent, virkestoff, atc FROM preparater ORDER BY navn"
        )
        .all() as Array<{
        id: number;
        navn: string;
        produsent: string | null;
        virkestoff: string | null;
        atc: string | null;
      }>;
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                antall: rows.length,
                preparater: rows,
                kilde: "Felleskatalogen.no — POC-utvalg av flaggskip-legemidler",
              },
              null,
              2
            ),
          },
        ],
      };
    }
  );
}
