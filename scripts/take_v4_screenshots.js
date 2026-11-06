import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const CHROME_PATH = "/home/alanrod10/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome";
const PORT = 9222;
const BASE_URL = "http://127.0.0.1:4173/";
const OUT_DIR = path.resolve("evidencias/v4_visual_evolution");

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
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
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

    // Create target page
    const newPageRes = await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(BASE_URL)}`, { method: "PUT" });
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

    // Wait for load
    await wait(1500);

    // If LoginModal is open, dismiss it to Modo Demo
    await cdp.eval(`
      const btnDemo = document.querySelector('[data-testid="btn-cancel-to-demo"]');
      if (btnDemo) {
        btnDemo.click();
      }
    `);
    await wait(1000);

    async function capture(name, width, height, theme = "dark") {
      // Set viewport
      await cdp.send("Emulation.setDeviceMetricsOverride", {
        width,
        height,
        deviceScaleFactor: 1,
        mobile: width <= 800,
      });

      // Set theme attribute
      await cdp.eval(`
        document.documentElement.setAttribute('data-theme', '${theme}');
        localStorage.setItem('genaro_pos_theme_pref', '${theme}');
      `);
      await wait(500);

      const screenshot = await cdp.send("Page.captureScreenshot", { format: "png" });
      const filePath = path.join(OUT_DIR, `${name}.png`);
      fs.writeFileSync(filePath, Buffer.from(screenshot.data, "base64"));
      console.log(`Saved screenshot: ${filePath} (${width}x${height}, ${theme})`);
    }

    // 1. CAJA ESCRITORIO (Dark & Light)
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-caja"]')?.click()`);
    await wait(600);
    // Add an item to cart to show items
    await cdp.eval(`document.querySelector('[data-testid="prod-card-1"]')?.click()`);
    await wait(500);
    await capture("01_caja_escritorio_dark", 1280, 800, "dark");
    await capture("02_caja_escritorio_light", 1280, 800, "light");
    await capture("03_caja_fullhd_dark", 1920, 1080, "dark");

    // Modal de Cobro (PaymentModal)
    await cdp.eval(`document.querySelector('[data-testid="btn-checkout"]')?.click()`);
    await wait(500);
    await capture("03b_caja_modal_cobro_dark", 1280, 800, "dark");
    await capture("03c_caja_modal_cobro_light", 1280, 800, "light");
    await cdp.eval(`document.querySelector('[data-testid="btn-close-payment"]')?.click()`);
    await wait(400);

    // 2. CAJA MÓVIL (Catálogo y Ticket)
    await capture("04_caja_movil_catalogo_dark", 390, 844, "dark");
    await capture("05_caja_movil_catalogo_light", 390, 844, "light");

    // Switch to ticket tab on mobile
    await cdp.eval(`document.querySelector('[data-testid="btn-tab-cart"]')?.click()`);
    await wait(500);
    await capture("06_caja_movil_ticket_dark", 390, 844, "dark");
    await capture("07_caja_movil_ticket_light", 390, 844, "light");

    // Switch back to catalog
    await cdp.eval(`document.querySelector('[data-testid="btn-tab-catalog"]')?.click()`);
    await wait(300);

    // 3. PRODUCTOS ESCRITORIO
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-productos"]')?.click()`);
    await wait(700);
    await capture("08_productos_escritorio_dark", 1280, 800, "dark");
    await capture("09_productos_escritorio_light", 1280, 800, "light");

    // 4. PRODUCTOS MÓVIL
    await capture("10_productos_movil_dark", 390, 844, "dark");
    await capture("11_productos_movil_light", 390, 844, "light");

    // 5. PRODUCTOS MODAL ALTA
    await cdp.eval(`document.querySelector('[data-testid="btn-new-product"]')?.click()`);
    await wait(500);
    await capture("12_productos_modal_alta_dark", 1280, 800, "dark");
    await capture("13_productos_modal_alta_light", 1280, 800, "light");
    await cdp.eval(`document.querySelector('[data-testid="btn-cancel-product"]')?.click()`);
    await wait(400);

    // 6. SERVICIOS ESCRITORIO
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-servicios"]')?.click()`);
    await wait(700);
    // Select a platform and destination to showcase full banner and breakdown
    await cdp.eval(`
      const platSelect = document.querySelector('[data-testid="select-plataforma-origen"]');
      if (platSelect) {
        platSelect.value = "PREPAY";
        platSelect.dispatchEvent(new Event("change", { bubbles: true }));
      }
    `);
    await wait(400);
    await cdp.eval(`
      const destSelect = document.querySelector('[data-testid="select-servicio-destino"]');
      if (destSelect) {
        destSelect.value = "SUBE";
        destSelect.dispatchEvent(new Event("change", { bubbles: true }));
      }
      const chip = document.querySelector('[data-testid="btn-chip-service-3000"]');
      if (chip) chip.click();
    `);
    await wait(500);
    await capture("14_servicios_escritorio_dark", 1280, 800, "dark");
    await capture("15_servicios_escritorio_light", 1280, 800, "light");

    // 7. SERVICIOS MÓVIL
    await capture("16_servicios_movil_dark", 390, 844, "dark");
    await capture("17_servicios_movil_light", 390, 844, "light");

    console.log("All screenshots captured successfully!");
    ws.close();
  } finally {
    chromeProc.kill();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
