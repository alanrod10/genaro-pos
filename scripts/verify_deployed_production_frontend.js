import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const CHROME_PATH = "/home/alanrod10/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome";
const PORT = 9222;
const OUT_DIR = path.resolve("evidencias/fase_v_post_deploy");

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
    this.consoleErrors = [];
    this.exceptions = [];

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
        this.consoleErrors.push(msg.params.args.map((a) => a.value || a.description).join(" "));
      }
      if (msg.method === "Runtime.exceptionThrown") {
        this.exceptions.push(msg.params.exceptionDetails?.text || JSON.stringify(msg.params));
      }
      if (msg.id && this.callbacks.has(msg.id)) {
        const { resolve, reject } = this.callbacks.get(msg.id);
        this.callbacks.delete(msg.id);
        if (msg.error) reject(msg.error);
        else resolve(msg.result);
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
    const res = await this.send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    return res.result?.value;
  }
}

async function verifyTarget(targetName, url) {
  console.log(`\n======================================================`);
  console.log(`Verifying target: ${targetName} (${url})`);
  console.log(`======================================================`);

  // Create target page
  const newPageRes = await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });
  const targetInfo = await newPageRes.json();
  const ws = new WebSocket(targetInfo.webSocketDebuggerUrl);

  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
  });

  const cdp = new CdpClient(ws);
  await cdp.send("Page.enable");
  await cdp.send("DOM.enable");
  await cdp.send("Runtime.enable");

  // Viewport 1440x900 desktop
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width: 1440,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  });

  // Wait for initial render
  await wait(2000);

  // 1. Verify Login Modal / Mode selector
  const hasLoginModal = await cdp.eval(`!!document.querySelector('[data-testid="login-modal-card"]')`);
  console.log(`[1] Login modal present on initial load: ${hasLoginModal}`);

  // Dismiss login to demo mode for safe UI testing
  await cdp.eval(`
    const btnDemo = document.querySelector('[data-testid="btn-cancel-to-demo"]');
    if (btnDemo) btnDemo.click();
  `);
  await wait(800);

  // 2. Verify Caja & Cart interactive controls
  await cdp.eval(`document.querySelector('[data-testid="nav-btn-caja"]')?.click()`);
  await wait(500);

  // Check product catalog cards
  const productCount = await cdp.eval(`document.querySelectorAll('.product-card').length`);
  console.log(`[2] Products rendered in catalog: ${productCount}`);

  // Click first product card
  await cdp.eval(`document.querySelector('[data-testid="prod-card-1"]')?.click()`);
  await wait(400);

  // Verify Cart items and aria labels
  const cartItemMinusLabel = await cdp.eval(`
    document.querySelector('[data-testid^="btn-qty-minus-"]')?.getAttribute('aria-label')
  `);
  const cartItemPlusLabel = await cdp.eval(`
    document.querySelector('[data-testid^="btn-qty-plus-"]')?.getAttribute('aria-label')
  `);
  const cartItemRemoveLabel = await cdp.eval(`
    document.querySelector('[data-testid^="btn-remove-"]')?.getAttribute('aria-label')
  `);
  console.log(`[3] Cart accessibility labels: minus="${cartItemMinusLabel}", plus="${cartItemPlusLabel}", remove="${cartItemRemoveLabel}"`);

  // Open Payment Modal
  await cdp.eval(`document.querySelector('[data-testid="btn-checkout"]')?.click()`);
  await wait(500);

  const paymentModalOpen = await cdp.eval(`!!document.querySelector('[data-testid="payment-modal"]')`);
  const btnClosePaymentLabel = await cdp.eval(`
    document.querySelector('[data-testid="btn-close-payment"]')?.getAttribute('aria-label')
  `);
  console.log(`[4] Payment modal open: ${paymentModalOpen}, close aria-label="${btnClosePaymentLabel}"`);

  // Test Esc key dismiss on Payment Modal
  await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await wait(500);

  const paymentModalClosed = await cdp.eval(`!document.querySelector('[data-testid="payment-modal"]')`);
  console.log(`[5] Payment modal closed by Escape key: ${paymentModalClosed}`);

  // 3. Verify Product Admin
  await cdp.eval(`document.querySelector('[data-testid="nav-btn-productos"]')?.click()`);
  await wait(600);
  const productsTableRows = await cdp.eval(`document.querySelectorAll('.admin-table tbody tr').length`);
  console.log(`[6] Product admin table rows: ${productsTableRows}`);

  // 4. Verify Services
  await cdp.eval(`document.querySelector('[data-testid="nav-btn-servicios"]')?.click()`);
  await wait(600);
  const servicesPlatformSelect = await cdp.eval(`!!document.querySelector('[data-testid="select-plataforma-origen"]')`);
  console.log(`[7] Services platform select available: ${servicesPlatformSelect}`);

  // 5. Verify Tesorería and Deferred Opening Protection
  await cdp.eval(`document.querySelector('[data-testid="nav-btn-tesoreria"]')?.click()`);
  await wait(800);

  // Check Disponibilidad view
  const tesoreriaHeader = await cdp.eval(`document.querySelector('[data-testid="tab-disponibilidad"]')?.textContent`);
  console.log(`[8] Tesorería Disponibilidad tab active: ${!!tesoreriaHeader}`);

  // Switch to Aperturas tab
  await cdp.eval(`document.querySelector('[data-testid="tab-aperturas"]')?.click()`);
  await wait(600);

  // Check Aperturas status badges
  const aperturasPendingBadges = await cdp.eval(`
    Array.from(document.querySelectorAll('.badge, span')).filter(el => el.textContent.includes('PENDIENTE') || el.textContent.includes('Pendiente')).length
  `);
  console.log(`[9] Tesorería pending opening badges found: ${aperturasPendingBadges}`);

  // 6. Verify Sync Queue modal
  await cdp.eval(`document.querySelector('[data-testid="btn-open-queue"]')?.click()`);
  await wait(500);
  const queueModalOpen = await cdp.eval(`!!document.querySelector('[data-testid="sync-queue-modal"]')`);
  const queueCloseLabel = await cdp.eval(`
    document.querySelector('[data-testid="btn-close-queue"]')?.getAttribute('aria-label')
  `);
  console.log(`[10] Sync queue modal open: ${queueModalOpen}, close aria-label="${queueCloseLabel}"`);

  // Close queue with Escape
  await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await wait(400);

  // Capture verification screenshot
  const shot = await cdp.send("Page.captureScreenshot", { format: "png" });
  const shotPath = path.join(OUT_DIR, `post_deploy_${targetName}.png`);
  fs.writeFileSync(shotPath, Buffer.from(shot.data, "base64"));
  console.log(`[11] Post-deploy screenshot saved: ${shotPath}`);

  // Report console errors
  console.log(`[12] Console errors: ${cdp.consoleErrors.length}`);
  if (cdp.consoleErrors.length > 0) {
    console.warn(`Console error details:`, cdp.consoleErrors);
  }
  console.log(`[13] Exceptions thrown: ${cdp.exceptions.length}`);
  if (cdp.exceptions.length > 0) {
    console.warn(`Exception details:`, cdp.exceptions);
  }

  ws.close();
  return {
    targetName,
    cartItemMinusLabel,
    paymentModalClosed,
    productsTableRows,
    queueModalOpen,
    consoleErrors: cdp.consoleErrors.length,
    exceptions: cdp.exceptions.length,
  };
}

async function main() {
  if (!fs.existsSync(OUT_DIR)) fs.mkdirSync(OUT_DIR, { recursive: true });

  const chromeProc = spawn(CHROME_PATH, [
    "--headless",
    "--no-sandbox",
    "--disable-gpu",
    `--remote-debugging-port=${PORT}`,
    "--hide-scrollbars",
    "--mute-audio",
  ]);

  try {
    const wsUrl = await getWsUrl();
    console.log("Connected to Chrome:", wsUrl);

    // Target 1: Caddy local production (http://127.0.0.1:8080/)
    const caddyResult = await verifyTarget("local_caddy_www", "http://127.0.0.1:8080/");

    // Target 2: Cloudflare Pages live (https://genaro-pos-live.pages.dev/)
    const pagesResult = await verifyTarget("cloudflare_pages_live", "https://genaro-pos-live.pages.dev/");

    console.log("\n======================================================");
    console.log("FINAL POST-DEPLOYMENT VERIFICATION SUMMARY");
    console.log("======================================================");
    console.log("Local Caddy (/home/alanrod10/production/www):", caddyResult);
    console.log("Cloudflare Pages (https://genaro-pos-live.pages.dev):", pagesResult);

    if (caddyResult.consoleErrors === 0 && pagesResult.consoleErrors === 0) {
      console.log("\n>>> ALL DESTINATIONS VERIFIED WITH 0 CRITICAL CONSOLE ERRORS <<<");
    } else {
      console.warn("\n>>> NOTICE: CONSOLE ERRORS DETECTED <<<");
    }
  } finally {
    chromeProc.kill();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
