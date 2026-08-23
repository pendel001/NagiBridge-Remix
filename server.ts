import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const LISTEN_PORT = 9000;
const CHAT_FILE = new URL("./chat_outbox.txt", import.meta.url).pathname.replace(/^\/([A-Z]:)/, "$1");

const server = new McpServer(
  { name: "game-overlay", version: "0.0.1" },
  {
    capabilities: { experimental: { "claude/channel": {} } },
    instructions: [
      '游戏overlay的消息会以 <channel source="game-overlay"> 的形式到达。',
      '用 reply tool 回复。pass chat_id back。',
    ].join("\n"),
  }
);

server.tool(
  "reply",
  "回复游戏overlay里的消息",
  { chat_id: z.string(), text: z.string() },
  async ({ chat_id, text }) => {
    await Bun.write(CHAT_FILE, JSON.stringify({ chat_id, text, ts: Date.now() }));
    return { content: [{ type: "text", text: `sent to overlay` }] };
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);

Bun.serve({
  port: LISTEN_PORT,
  hostname: "127.0.0.1",
  async fetch(req: Request) {
    if (req.method !== "POST") {
      return new Response("use POST", { status: 405 });
    }

    try {
      const body = await req.json();
      const message = body.message || "";
      const chatId = body.chat_id || "game1";

      if (!message.trim()) {
        return new Response("empty message", { status: 400 });
      }

      const mcpServer = (server as any).server;
      await mcpServer.notification({
        method: "notifications/claude/channel",
        params: {
          content: `<channel source="game-overlay" chat_id="${chatId}">${message}</channel>`,
        },
      });

      return Response.json({ ok: true });
    } catch (e: any) {
      return new Response(e.message, { status: 500 });
    }
  },
});
