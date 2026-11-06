import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const CHROME_PATH = "/home/alanrod10/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome";
const PORT = 9224;
const LIVE_URL = "https://genaro-pos-live.pages.dev/";
const OUT_DIR = path.resolve("evidencias/v4_live_production");

async function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function getWsUrl() {
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      const data = await res.json();
      if (data.webSocketDebuggerUrl) return data.webSocketDebuggerUrl;
    } catch {}
    await wait(200);
  }
  throw new Error("Could not connect to Chrome debugging port");
}

class CdpClient {
  constructor(ws) {
    this.ws = ws;
    this.id = 1;
    this.callbacks = new Map();
    this.consoleLogs = [];
    this.networkErrors = [];

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && this.callbacks.has(msg.id)) {
        const { resolve, reject } = this.callbacks.get(msg.id);
        this.callbacks.delete(msg.id);
        if (msg.error) reject(msg.error);
        else resolve(msg.result);
      } else if (msg.method) {
        if (msg.method === "Runtime.consoleAPICalled") {
          this.consoleLogs.push(msg.params);
        } else if (msg.method === "Network.responseReceived") {
          const { response } = msg.params;
          if (response.status >= 400) {
            this.networkErrors.push({ url: response.url, status: response.status });
          }
        }
      }
    };
  }

  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = this.id++;
      this.callbacks.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async eval(expr) {
    const res = await this.send("Runtime.evaluate", {
      expression: expr,
      returnByValue: true,
      awaitPromise: true,
    });
    if (res.exceptionDetails) {
      throw new Error(JSON.stringify(res.exceptionDetails));
    }
    return res.result ? res.result.value : undefined;
  }

  async setViewport(width, height, isMobile = false) {
    await this.send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: 2,
      mobile: isMobile,
    });
    await this.send("Emulation.setVisibleSize", { width, height });
    await wait(300);
  }

  async screenshot(filename) {
    const res = await this.send("Page.captureScreenshot", { format: "png" });
    const buffer = Buffer.from(res.data, "base64");
    const fullPath = path.join(OUT_DIR, filename);
    fs.writeFileSync(fullPath, buffer);
    console.log(`Saved live screenshot: ${fullPath}`);
  }
}

async function main() {
  if (!fs.existsSync(OUT_DIR)) {
    fs.mkdirSync(OUT_DIR, { recursive: true });
  }

  const chromeProc = spawn(CHROME_PATH, [
    "--headless",
    "--no-sandbox",
    "--disable-gpu",
    `--remote-debugging-port=${PORT}`,
    "--hide-scrollbars",
    "--mute-audio",
  ]);

  try {
    await getWsUrl();
    console.log(`Navigating to live production: ${LIVE_URL}`);
    const newPageRes = await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(LIVE_URL)}`, { method: "PUT" });
    const targetInfo = await newPageRes.json();
    const ws = new WebSocket(targetInfo.webSocketDebuggerUrl);

    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = reject;
    });

    const client = new CdpClient(ws);
    await client.send("Page.enable");
    await client.send("DOM.enable");
    await client.send("Runtime.enable");
    await client.send("Network.enable");

    await wait(3000);

    // Cancel modal to demo mode
    await client.eval(`
      (() => {
        const btn = document.querySelector('[data-testid="btn-cancel-to-demo"]') || 
                    Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Demo'));
        if (btn) btn.click();
      })()
    `);
    await wait(1200);

    // 1. Caja Escritorio Dark
    await client.setViewport(1280, 800, false);
    await client.eval(`document.documentElement.setAttribute('data-theme', 'dark')`);
    await wait(300);
    await client.screenshot("live_01_caja_escritorio_dark.png");

    // 2. Caja Escritorio Light
    await client.eval(`document.documentElement.setAttribute('data-theme', 'light')`);
    await wait(300);
    await client.screenshot("live_02_caja_escritorio_light.png");

    // 3. Caja Móvil Catálogo Dark
    await client.setViewport(390, 844, true);
    await client.eval(`document.documentElement.setAttribute('data-theme', 'dark')`);
    await wait(300);
    await client.screenshot("live_03_caja_movil_catalogo_dark.png");

    // 4. Productos Escritorio Dark
    await client.setViewport(1280, 800, false);
    await client.eval(`
      (() => {
        const btn = Array.from(document.querySelectorAll('button, a')).find(el => el.textContent.includes('Productos'));
        if (btn) btn.click();
      })()
    `);
    await wait(1200);
    await client.screenshot("live_04_productos_escritorio_dark.png");

    // 5. Productos Móvil Dark
    await client.setViewport(390, 844, true);
    await wait(400);
    await client.screenshot("live_05_productos_movil_dark.png");

    // 6. Servicios Escritorio Dark
    await client.setViewport(1280, 800, false);
    await client.eval(`
      (() => {
        const btn = Array.from(document.querySelectorAll('button, a')).find(el => el.textContent.includes('Servicios'));
        if (btn) btn.click();
      })()
    `);
    await wait(1200);
    await client.screenshot("live_06_servicios_escritorio_dark.png");

    // 7. Servicios Móvil Dark
    await client.setViewport(390, 844, true);
    await wait(400);
    await client.screenshot("live_07_servicios_movil_dark.png");

    // Check bundle hashes from live DOM
    const liveAssets = await client.eval(`
      Array.from(document.querySelectorAll('script[src], link[rel="stylesheet"]')).map(el => el.src || el.href)
    `);
    console.log("Live assets loaded in DOM:", liveAssets);

    console.log(`Console logs recorded: ${client.consoleLogs.length}`);
    console.log(`Network errors recorded: ${client.networkErrors.length}`);
    if (client.networkErrors.length > 0) {
      console.log("Network Errors:", client.networkErrors);
    }

    console.log("Live production verification completed successfully!");
  } finally {
    chromeProc.kill("SIGTERM");
  }
}

main().catch((err) => {
  console.error("Verification failed:", err);
  process.exit(1);
});
