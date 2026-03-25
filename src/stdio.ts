import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createServer } from "./tools.js";

async function main() {
  if (!process.env.HAPI_SUBSCRIPTION_KEY) {
    console.error("HAPI_SUBSCRIPTION_KEY environment variable is not set.");
    process.exit(1);
  }

  const server = createServer();
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
