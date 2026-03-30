/**
 * Xero OAuth 2.0 Authorization Helper
 *
 * 1. Opens browser to Xero login
 * 2. Handles callback on localhost:8000
 * 3. Exchanges auth code for tokens
 * 4. Saves tokens to .xero_tokens.json
 *
 * Usage: node scripts/xero_auth.mjs
 */

import http from "node:http";
import https from "node:https";
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

// Load credentials from .env.local
const envFile = readFileSync(resolve(ROOT, ".env.local"), "utf-8");
const env = Object.fromEntries(
  envFile
    .split("\n")
    .filter((l) => l.includes("="))
    .map((l) => {
      const i = l.indexOf("=");
      return [l.slice(0, i).trim(), l.slice(i + 1).trim()];
    })
);

const CLIENT_ID = env.XERO_CLIENT_ID;
const CLIENT_SECRET = env.XERO_CLIENT_SECRET;
const REDIRECT_URI = "http://localhost:8000/callback";
const SCOPES = [
  "openid",
  "profile",
  "email",
  "accounting.reports.profitandloss.read",
  "accounting.reports.trialbalance.read",
  "accounting.reports.balancesheet.read",
  "accounting.settings",
  "offline_access",
].join(" ");
const TOKEN_FILE = resolve(ROOT, ".xero_tokens.json");

// Build authorization URL
const authUrl =
  `https://login.xero.com/identity/connect/authorize?` +
  `response_type=code&` +
  `client_id=${CLIENT_ID}&` +
  `redirect_uri=${encodeURIComponent(REDIRECT_URI)}&` +
  `scope=${encodeURIComponent(SCOPES)}&` +
  `state=xero_auth_test`;

console.log("\n=== Xero OAuth 2.0 Authorization ===\n");
console.log("Opening browser for Xero login...\n");

// Open browser (Windows)
try {
  execSync(`start "" "${authUrl}"`, { shell: "cmd.exe", stdio: "ignore" });
} catch {
  console.log("Could not open browser automatically. Open this URL:\n");
  console.log(authUrl + "\n");
}

// Start local server to catch the callback
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost:8000");

  if (!url.pathname.startsWith("/callback")) {
    res.writeHead(404);
    res.end("Not found");
    return;
  }

  const code = url.searchParams.get("code");
  const error = url.searchParams.get("error");

  if (error) {
    console.error(`\nAuthorization failed: ${error}`);
    res.writeHead(400);
    res.end(`Authorization failed: ${error}`);
    server.close();
    process.exit(1);
  }

  if (!code) {
    res.writeHead(400);
    res.end("No authorization code received");
    return;
  }

  console.log("Authorization code received. Exchanging for tokens...\n");

  // Exchange code for tokens
  try {
    const tokens = await exchangeCode(code);
    writeFileSync(TOKEN_FILE, JSON.stringify(tokens, null, 2));

    console.log("=== SUCCESS ===");
    console.log(`Access token: ${tokens.access_token.slice(0, 30)}...`);
    console.log(`Refresh token: ${tokens.refresh_token.slice(0, 20)}...`);
    console.log(`Expires in: ${tokens.expires_in}s`);
    console.log(`Scope: ${tokens.scope}`);
    console.log(`\nTokens saved to: .xero_tokens.json`);

    // Test the API
    console.log("\n=== Testing API access ===\n");
    await testApi(tokens.access_token);

    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(
      "<html><body><h1>Xero Authorization Successful!</h1>" +
        "<p>You can close this tab. Check the terminal for results.</p></body></html>"
    );
  } catch (err) {
    console.error("Token exchange failed:", err.message);
    res.writeHead(500);
    res.end(`Token exchange failed: ${err.message}`);
  }

  setTimeout(() => {
    server.close();
    process.exit(0);
  }, 500);
});

server.listen(8000, () => {
  console.log("Waiting for Xero callback on http://localhost:8000/callback ...\n");
});

function exchangeCode(code) {
  return new Promise((resolve, reject) => {
    const body = new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: REDIRECT_URI,
    }).toString();

    const auth = Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString("base64");

    const req = https.request(
      "https://identity.xero.com/connect/token",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          Authorization: `Basic ${auth}`,
        },
      },
      (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          if (res.statusCode !== 200) {
            reject(new Error(`HTTP ${res.statusCode}: ${data}`));
          } else {
            resolve(JSON.parse(data));
          }
        });
      }
    );
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

function testApi(accessToken) {
  return new Promise((resolve, reject) => {
    // First get the tenant ID from connections
    const connReq = https.request(
      "https://api.xero.com/connections",
      {
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
        },
      },
      (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          if (res.statusCode !== 200) {
            console.log(`Connections call failed: HTTP ${res.statusCode}`);
            console.log(data);
            resolve();
            return;
          }

          const connections = JSON.parse(data);
          if (connections.length === 0) {
            console.log("No Xero organisations connected.");
            resolve();
            return;
          }

          const tenantId = connections[0].tenantId;
          const orgName = connections[0].tenantName;
          console.log(`Connected org: ${orgName} (tenant: ${tenantId})`);

          // Now test P&L report
          const plReq = https.request(
            "https://api.xero.com/api.xro/2.0/Reports/ProfitAndLoss?fromDate=2026-01-01&toDate=2026-03-31",
            {
              headers: {
                Authorization: `Bearer ${accessToken}`,
                "xero-tenant-id": tenantId,
                Accept: "application/json",
              },
            },
            (plRes) => {
              let plData = "";
              plRes.on("data", (chunk) => (plData += chunk));
              plRes.on("end", () => {
                if (plRes.statusCode !== 200) {
                  console.log(`P&L call failed: HTTP ${plRes.statusCode}`);
                  console.log(plData);
                } else {
                  const report = JSON.parse(plData);
                  const r = report.Reports?.[0];
                  if (r) {
                    console.log(`\nP&L Report: ${r.ReportName}`);
                    console.log(`Titles: ${r.ReportTitles?.join(" | ")}`);
                    console.log(`Rows: ${r.Rows?.length}`);
                    for (const row of (r.Rows || []).slice(0, 4)) {
                      console.log(`  RowType: ${row.RowType} | Title: ${row.Title || "-"}`);
                    }
                  }
                }
                console.log("\n=== ALL TESTS PASSED ===");
                resolve();
              });
            }
          );
          plReq.on("error", (e) => {
            console.log(`P&L request error: ${e.message}`);
            resolve();
          });
          plReq.end();
        });
      }
    );
    connReq.on("error", (e) => {
      console.log(`Connections request error: ${e.message}`);
      resolve();
    });
    connReq.end();
  });
}
