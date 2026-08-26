import { readFile } from "node:fs/promises";
import { ReplitConnectors } from "@replit/connectors-sdk";

try {
  const { path } = JSON.parse(await readFile(0, "utf8"));
  if (typeof path !== "string" || !path.startsWith("/v4/spreadsheets/")) {
    throw new Error("Invalid Google Sheets API path.");
  }

  const connectors = new ReplitConnectors();
  const response = await connectors.proxy("google-sheet", path, { method: "GET" });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};

  process.stdout.write(JSON.stringify({
    ok: response.ok,
    status: response.status,
    data: response.ok ? data : undefined,
  }));
  process.exitCode = response.ok ? 0 : 1;
} catch {
  process.stdout.write(JSON.stringify({ ok: false }));
  process.exitCode = 1;
}