/**
 * MCP-tools for NDLA-innhold om "Helsefremmende arbeid (HS-HEA vg2)".
 *
 * Innholdet ligger i en SQLite-fil (data/ndla_helsefag.db) som bygges av
 * `ndla-scraper/scrape.py`. Basen har FTS5 over title + introduction +
 * content_text + tags, og er indeksert på topic og resource-type.
 *
 * Lisens: alt innhold er CC-BY-SA-4.0 fra NDLA. Verktøyene returnerer
 * kildelenker slik at agenter kan kreditere korrekt.
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
  const override = process.env.NDLA_DB_PATH;
  if (override && existsSync(override)) return override;
  // Candidates — first one that exists wins
  const candidates = [
    resolve(__dirname, "../ndla-scraper/data/ndla_helsefag.db"), // dev: dist/ -> repo-root/ndla-scraper/...
    resolve(__dirname, "../../ndla-scraper/data/ndla_helsefag.db"),
    resolve(__dirname, "./ndla_helsefag.db"), // prod: kopiert inn ved siden av dist
    resolve(process.cwd(), "ndla-scraper/data/ndla_helsefag.db"),
    resolve(process.cwd(), "ndla_helsefag.db"),
  ];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return candidates[0]; // fall through — vi lar feilen komme når DB-en faktisk åpnes
}

let db: Database.Database | null = null;
function getDb(): Database.Database {
  if (db) return db;
  const path = resolveDbPath();
  if (!existsSync(path)) {
    throw new Error(
      `NDLA-database ikke funnet. Forventet ${path}. Kjør 'python ndla-scraper/scrape.py' eller sett NDLA_DB_PATH.`
    );
  }
  db = new Database(path, { readonly: true, fileMustExist: true });
  db.pragma("query_only = true");
  return db;
}

// FTS5 matcher spesialtegn — vi escaper ved å sitere termer
function toFtsQuery(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return '""';
  // Splitt på whitespace, sorter ut støyord, siter hver term som phrase
  const terms = trimmed
    .split(/\s+/)
    .filter((t) => t.length > 0 && !/^[-+()"*]+$/.test(t))
    .map((t) => {
      // Fjern " internt, og omslutt med "
      const safe = t.replace(/"/g, "");
      return `"${safe}"`;
    });
  if (terms.length === 0) return '""';
  return terms.join(" AND ");
}

interface SearchRow {
  article_id: number;
  title: string;
  introduction: string | null;
  primary_type: string | null;
  resource_name: string | null;
  topic_name: string | null;
  breadcrumbs: string | null;
  url: string | null;
  license: string | null;
  snippet: string;
  rank: number;
}

export function registerNdlaTools(server: McpServer): void {
  server.tool(
    "sok_ndla_helsefag",
    "Søk i NDLAs fagstoff for 'Helsefremmende arbeid (HS-HEA vg2)' — pensum for helsefagarbeidere. " +
      "Dekker bl.a. helse og mestring, smittevern og hygiene, helhetlig omsorg og pleie, ernæring, " +
      "sykdom og helsesvikt, prosedyrer og førstehjelp. Returnerer topp treff med tittel, " +
      "tema-breadcrumb, utdrag og kilde-URL. Kilde: NDLA (CC-BY-SA-4.0).",
    {
      queryText: z.string().describe("Fritekst-søk. Eks: 'håndhygiene', 'sårstell hos eldre'"),
      maxResults: z
        .number()
        .optional()
        .describe("Maks antall resultater (default 10, maks 30)"),
      resourceType: z
        .enum(["Fagstoff", "Oppgave", "Kildemateriell", "Læringssti", "Spill"])
        .optional()
        .describe("Filtrer på ressurstype. 'Fagstoff' er fagtekst, 'Oppgave' er øvelser."),
      topic: z
        .string()
        .optional()
        .describe(
          "Filtrer på hovedtema (fuzzy match på breadcrumb). Eks: 'Smittevern', 'Ernæring', 'Førstehjelp'"
        ),
    },
    async ({ queryText, maxResults, resourceType, topic }) => {
      const database = getDb();
      const top = Math.min(maxResults ?? 10, 30);
      const fts = toFtsQuery(queryText);

      const where: string[] = ["articles_fts MATCH ?"];
      const params: unknown[] = [fts];
      if (resourceType) {
        where.push("r.primary_type = ?");
        params.push(resourceType);
      }
      if (topic) {
        where.push("t.breadcrumbs LIKE ?");
        params.push(`%${topic}%`);
      }

      // FTS5 snippet() krever direkte tilgang til virtuell tabell uten GROUP BY —
      // så vi henter FTS-treff + snippet i en CTE og joiner mot metadata etterpå.
      const sql = `
        WITH hits AS (
          SELECT
            articles_fts.rowid AS article_id,
            snippet(articles_fts, 2, '<<', '>>', ' … ', 24) AS snippet,
            rank
          FROM articles_fts
          WHERE articles_fts MATCH ?
          ORDER BY rank
          LIMIT ?
        )
        SELECT
          a.id AS article_id,
          a.title AS title,
          a.introduction AS introduction,
          r.primary_type AS primary_type,
          r.name AS resource_name,
          t.name AS topic_name,
          t.breadcrumbs AS breadcrumbs,
          COALESCE(r.url, t.url) AS url,
          a.license AS license,
          h.snippet AS snippet,
          h.rank AS rank
        FROM hits h
        JOIN articles a ON a.id = h.article_id
        LEFT JOIN resources r ON r.article_id = a.id
        LEFT JOIN topics t ON t.id = r.topic_id
        ${where.length > 1 ? "WHERE " + where.slice(1).join(" AND ") : ""}
        GROUP BY a.id
        ORDER BY h.rank
        LIMIT ?
      `;
      const filterParams = params.slice(1);
      const preFilterLimit = (resourceType || topic) ? Math.min(top * 5, 200) : top;
      params.length = 0;
      params.push(fts, preFilterLimit, ...filterParams, top);

      const rows = database.prepare(sql).all(...params) as SearchRow[];

      const output = {
        query: queryText,
        treff: rows.length,
        resultater: rows.map((r) => ({
          artikkelId: r.article_id,
          tittel: r.title,
          tema: r.topic_name,
          breadcrumb: r.breadcrumbs ? JSON.parse(r.breadcrumbs) : [],
          ressurstype: r.primary_type,
          introduksjon: r.introduction,
          utdrag: r.snippet,
          url: r.url,
          lisens: r.license,
        })),
        kilde: "NDLA — Helsefremmende arbeid (HS-HEA vg2). Innhold er CC-BY-SA-4.0.",
      };

      return { content: [{ type: "text", text: JSON.stringify(output, null, 2) }] };
    }
  );

  server.tool(
    "hent_ndla_artikkel",
    "Hent full NDLA-artikkel (ren tekst) basert på artikkel-ID fra sok_ndla_helsefag. " +
      "Returnerer tittel, introduksjon, full tekst, forfattere, tags, og tema-tilhørighet.",
    {
      artikkelId: z.number().describe("Artikkel-ID (numerisk) fra sok_ndla_helsefag-treffene"),
      format: z
        .enum(["tekst", "html"])
        .optional()
        .describe("Returner ren tekst (default) eller original HTML"),
    },
    async ({ artikkelId, format }) => {
      const database = getDb();
      const row = database
        .prepare(
          `SELECT a.*, r.name AS resource_name, r.primary_type, r.url AS resource_url,
                  t.name AS topic_name, t.breadcrumbs AS breadcrumbs
           FROM articles a
           LEFT JOIN resources r ON r.article_id = a.id
           LEFT JOIN topics t ON t.id = r.topic_id
           WHERE a.id = ?
           LIMIT 1`
        )
        .get(artikkelId) as
        | {
            id: number;
            title: string;
            introduction: string | null;
            meta_description: string | null;
            content_html: string;
            content_text: string;
            article_type: string | null;
            license: string | null;
            license_url: string | null;
            authors: string | null;
            tags: string | null;
            grep_codes: string | null;
            updated: string | null;
            resource_name: string | null;
            primary_type: string | null;
            resource_url: string | null;
            topic_name: string | null;
            breadcrumbs: string | null;
          }
        | undefined;

      if (!row) {
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                { feil: `Fant ingen artikkel med ID ${artikkelId}` },
                null,
                2
              ),
            },
          ],
        };
      }

      const body = format === "html" ? row.content_html : row.content_text;
      const output = {
        artikkelId: row.id,
        tittel: row.title,
        introduksjon: row.introduction,
        metaBeskrivelse: row.meta_description,
        ressurstype: row.primary_type,
        ressursnavn: row.resource_name,
        tema: row.topic_name,
        breadcrumb: row.breadcrumbs ? JSON.parse(row.breadcrumbs) : [],
        artikkelType: row.article_type,
        tags: row.tags ? JSON.parse(row.tags) : [],
        forfattere: row.authors ? JSON.parse(row.authors) : [],
        grepKoder: row.grep_codes ? JSON.parse(row.grep_codes) : [],
        sistOppdatert: row.updated,
        url: row.resource_url,
        lisens: row.license,
        lisensUrl: row.license_url,
        innhold: body,
        kilde: "NDLA (https://ndla.no). Innhold er CC-BY-SA-4.0.",
      };

      return { content: [{ type: "text", text: JSON.stringify(output, null, 2) }] };
    }
  );

  server.tool(
    "hent_ndla_temaer",
    "Liste alle tema og undertema i NDLAs 'Helsefremmende arbeid (HS-HEA vg2)'. " +
      "Bruk dette for å se hele faginndelingen og finne tema-navn til sok_ndla_helsefag.",
    {
      detaljert: z
        .boolean()
        .optional()
        .describe("Inkluder antall ressurser per tema (default false)"),
    },
    async ({ detaljert }) => {
      const database = getDb();
      const rows = database
        .prepare(
          `SELECT t.id, t.parent_id, t.name, t.url, t.breadcrumbs, t.node_type,
                  (SELECT COUNT(*) FROM resources r WHERE r.topic_id = t.id) AS resource_count
           FROM topics t
           ORDER BY t.depth, t.name`
        )
        .all() as Array<{
        id: string;
        parent_id: string | null;
        name: string;
        url: string | null;
        breadcrumbs: string | null;
        node_type: string;
        resource_count: number;
      }>;

      type TopicNode = {
        id: string;
        navn: string;
        url: string | null;
        antallRessurser?: number;
        undertemaer: TopicNode[];
      };
      const map = new Map<string, TopicNode>();
      for (const r of rows) {
        map.set(r.id, {
          id: r.id,
          navn: r.name,
          url: r.url,
          ...(detaljert ? { antallRessurser: r.resource_count } : {}),
          undertemaer: [],
        });
      }
      const roots: TopicNode[] = [];
      for (const r of rows) {
        const node = map.get(r.id)!;
        if (r.parent_id && map.has(r.parent_id)) {
          map.get(r.parent_id)!.undertemaer.push(node);
        } else {
          roots.push(node);
        }
      }

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                fag: "Helsefremmende arbeid (HS-HEA vg2)",
                antallTemaer: rows.filter((r) => r.node_type !== "SUBJECT").length,
                struktur: roots,
                kilde: "NDLA",
              },
              null,
              2
            ),
          },
        ],
      };
    }
  );

  server.tool(
    "list_ndla_ressurser_for_tema",
    "List alle ressurser (artikler/oppgaver) under et gitt NDLA-tema. " +
      "Bruk tema-ID fra hent_ndla_temaer, eller søk fritekst med temaNavn.",
    {
      temaId: z
        .string()
        .optional()
        .describe("Tema-URN fra hent_ndla_temaer, eks. 'urn:topic:2:84f3947f-...'"),
      temaNavn: z
        .string()
        .optional()
        .describe("Tema-navn (fuzzy). Brukes hvis temaId ikke gis."),
      ressurstype: z
        .enum(["Fagstoff", "Oppgave", "Kildemateriell", "Læringssti", "Spill"])
        .optional(),
      maxResults: z.number().optional().describe("Maks (default 50, maks 200)"),
    },
    async ({ temaId, temaNavn, ressurstype, maxResults }) => {
      if (!temaId && !temaNavn) {
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                { feil: "Må oppgi enten temaId eller temaNavn" },
                null,
                2
              ),
            },
          ],
        };
      }

      const database = getDb();
      const top = Math.min(maxResults ?? 50, 200);

      let topicIds: string[] = [];
      if (temaId) {
        topicIds = [temaId];
        // inkluder alle undertemaer (ett nivå rekursjon er typisk nok, men vi gjør full lukking)
        const found = new Set<string>(topicIds);
        let frontier = [...topicIds];
        while (frontier.length) {
          const placeholders = frontier.map(() => "?").join(",");
          const children = database
            .prepare(`SELECT id FROM topics WHERE parent_id IN (${placeholders})`)
            .all(...frontier) as { id: string }[];
          frontier = [];
          for (const c of children) {
            if (!found.has(c.id)) {
              found.add(c.id);
              frontier.push(c.id);
              topicIds.push(c.id);
            }
          }
        }
      } else if (temaNavn) {
        const matches = database
          .prepare(`SELECT id FROM topics WHERE name LIKE ? OR breadcrumbs LIKE ?`)
          .all(`%${temaNavn}%`, `%${temaNavn}%`) as { id: string }[];
        topicIds = matches.map((m) => m.id);
        if (topicIds.length === 0) {
          return {
            content: [
              {
                type: "text",
                text: JSON.stringify(
                  { feil: `Fant ingen tema som matcher '${temaNavn}'`, treff: 0 },
                  null,
                  2
                ),
              },
            ],
          };
        }
      }

      const placeholders = topicIds.map(() => "?").join(",");
      const params: unknown[] = [...topicIds];
      let typeFilter = "";
      if (ressurstype) {
        typeFilter = " AND r.primary_type = ?";
        params.push(ressurstype);
      }
      params.push(top);

      const rows = database
        .prepare(
          `SELECT r.id, r.name, r.primary_type, r.url, r.article_id,
                  t.name AS topic_name, t.breadcrumbs,
                  a.introduction
           FROM resources r
           LEFT JOIN topics t ON t.id = r.topic_id
           LEFT JOIN articles a ON a.id = r.article_id
           WHERE r.topic_id IN (${placeholders})${typeFilter}
           ORDER BY t.name, r.primary_type, r.name
           LIMIT ?`
        )
        .all(...params) as Array<{
        id: string;
        name: string;
        primary_type: string | null;
        url: string | null;
        article_id: number | null;
        topic_name: string | null;
        breadcrumbs: string | null;
        introduction: string | null;
      }>;

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                treff: rows.length,
                ressurser: rows.map((r) => ({
                  ressursId: r.id,
                  artikkelId: r.article_id,
                  navn: r.name,
                  type: r.primary_type,
                  tema: r.topic_name,
                  breadcrumb: r.breadcrumbs ? JSON.parse(r.breadcrumbs) : [],
                  introduksjon: r.introduction,
                  url: r.url,
                })),
                kilde: "NDLA",
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
