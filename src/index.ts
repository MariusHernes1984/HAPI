import express from "express";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { createServer } from "./tools.js";

const PORT = parseInt(process.env.PORT ?? "3000", 10);
const API_KEY = process.env.MCP_API_KEY ?? "";

const app = express();
app.use(express.json());

// API key authentication middleware
app.use("/mcp", (req, res, next) => {
  if (API_KEY) {
    const provided =
      req.headers["x-api-key"] ??
      req.headers["api-key"] ??
      req.headers["ocp-apim-subscription-key"] ??
      req.headers["authorization"]?.replace("Bearer ", "");
    if (provided !== API_KEY) {
      console.warn(`401 Unauthorized — header keys: [${Object.keys(req.headers).join(", ")}]`);
      res.status(401).json({ error: "Unauthorized" });
      return;
    }
  }
  next();
});

// Health check
app.get("/health", (_req, res) => {
  res.json({ status: "ok", server: "hapi-helsedirektoratet" });
});

// Streamable HTTP transport on /mcp
app.post("/mcp", async (req, res) => {
  const server = createServer();
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
  });
  res.on("close", () => {
    transport.close();
    server.close();
  });
  await server.connect(transport);
  await transport.handleRequest(req, res, req.body);
});

app.listen(PORT, () => {
  console.log(`HAPI MCP server listening on http://localhost:${PORT}`);
  console.log(`MCP endpoint: POST http://localhost:${PORT}/mcp`);
  console.log(`Health check: GET http://localhost:${PORT}/health`);
});
