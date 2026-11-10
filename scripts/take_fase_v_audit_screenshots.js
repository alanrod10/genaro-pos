import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const CHROME_PATH = "/home/alanrod10/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome";
const PORT = 9222;
const BASE_URL = "http://127.0.0.1:4173/";
const OUT_DIR = path.resolve("evidencias/fase_v_visual_audit");

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

    // Wait for initial load
    await wait(1800);

    // If LoginModal is open, dismiss it to Modo Demo
    await cdp.eval(`
      const btnDemo = document.querySelector('[data-testid="btn-cancel-to-demo"]');
      if (btnDemo) {
        btnDemo.click();
      }
    `);
    await wait(800);

    async function capture(name, width, height, theme = "dark") {
      await cdp.send("Emulation.setDeviceMetricsOverride", {
        width,
        height,
        deviceScaleFactor: 1,
        mobile: width <= 800,
      });

      await cdp.eval(`
        document.documentElement.setAttribute('data-theme', '${theme}');
        localStorage.setItem('genaro_pos_theme_pref', '${theme}');
      `);
      await wait(450);

      const screenshot = await cdp.send("Page.captureScreenshot", { format: "png" });
      const filePath = path.join(OUT_DIR, `${name}.png`);
      fs.writeFileSync(filePath, Buffer.from(screenshot.data, "base64"));
      console.log(`Saved screenshot: ${filePath} (${width}x${height}, ${theme})`);
    }

    // --- 1. CAJA Y VENTAS ---
    console.log("Capturing 1. Caja y Ventas...");
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-caja"]')?.click()`);
    await wait(600);
    // Add product to cart to display populated ticket
    await cdp.eval(`document.querySelector('[data-testid="prod-card-1"]')?.click()`);
    await wait(400);

    await capture("01_caja_desktop_1440_dark", 1440, 900, "dark");
    await capture("02_caja_desktop_1440_light", 1440, 900, "light");
    await capture("03_caja_mobile_320_dark", 320, 568, "dark");
    await capture("04_caja_mobile_390_dark", 390, 844, "dark");

    // Switch to ticket tab on mobile
    await cdp.eval(`document.querySelector('[data-testid="btn-tab-cart"]')?.click()`);
    await wait(400);
    await capture("05_caja_mobile_390_ticket_dark", 390, 844, "dark");
    await cdp.eval(`document.querySelector('[data-testid="btn-tab-catalog"]')?.click()`);
    await wait(300);

    await capture("06_caja_tablet_768_dark", 768, 1024, "dark");
    await capture("07_caja_fhd_1920_dark", 1920, 1080, "dark");

    // Modal Cobro
    await cdp.eval(`document.querySelector('[data-testid="btn-checkout"]')?.click()`);
    await wait(500);
    await capture("08_modal_cobro_desktop_1440_dark", 1440, 900, "dark");
    await capture("09_modal_cobro_desktop_1440_light", 1440, 900, "light");
    await capture("10_modal_cobro_mobile_390_dark", 390, 844, "dark");
    await cdp.eval(`document.querySelector('[data-testid="btn-close-payment"]')?.click()`);
    await wait(400);

    // --- 2. PRODUCTOS ---
    console.log("Capturing 2. Catálogo de Productos...");
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-productos"]')?.click()`);
    await wait(700);
    await capture("11_productos_desktop_1440_dark", 1440, 900, "dark");
    await capture("12_productos_mobile_390_dark", 390, 844, "dark");

    // Modal Alta de Producto
    await cdp.eval(`document.querySelector('[data-testid="btn-new-product"]')?.click()`);
    await wait(500);
    await capture("13_productos_modal_alta_dark", 1440, 900, "dark");
    await cdp.eval(`document.querySelector('[data-testid="btn-cancel-product"]')?.click()`);
    await wait(400);

    // --- 3. SERVICIOS VIRTUALES ---
    console.log("Capturing 3. Servicios Virtuales...");
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-servicios"]')?.click()`);
    await wait(700);
    await cdp.eval(`
      const platSelect = document.querySelector('[data-testid="select-plataforma-origen"]');
      if (platSelect) {
        platSelect.value = "PREPAY";
        platSelect.dispatchEvent(new Event("change", { bubbles: true }));
      }
    `);
    await wait(300);
    await cdp.eval(`
      const destSelect = document.querySelector('[data-testid="select-servicio-destino"]');
      if (destSelect) {
        destSelect.value = "SUBE";
        destSelect.dispatchEvent(new Event("change", { bubbles: true }));
      }
      const chip = document.querySelector('[data-testid="btn-chip-service-3000"]');
      if (chip) chip.click();
    `);
    await wait(400);
    await capture("14_servicios_desktop_1440_dark", 1440, 900, "dark");
    await capture("15_servicios_mobile_390_dark", 390, 844, "dark");

    // --- 4. GASTOS Y ARQUEO ---
    console.log("Capturing 4. Gastos y Arqueo...");
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-gastos"]')?.click()`);
    await wait(700);
    await capture("16_gastos_desktop_1440_dark", 1440, 900, "dark");
    await capture("17_gastos_mobile_390_dark", 390, 844, "dark");

    // Sub-pestaña Arqueo
    await cdp.eval(`document.querySelector('[data-testid="tab-btn-arqueo"]')?.click()`);
    await wait(500);
    await capture("18_gastos_arqueo_desktop_1440_dark", 1440, 900, "dark");
    await cdp.eval(`document.querySelector('[data-testid="tab-btn-gastos"]')?.click()`);
    await wait(300);

    // --- 5. HISTORIAL DE VENTAS ---
    console.log("Capturing 5. Historial de Ventas...");
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-historial"]')?.click()`);
    await wait(700);
    await capture("19_historial_desktop_1440_dark", 1440, 900, "dark");
    await capture("20_historial_mobile_390_dark", 390, 844, "dark");

    // --- 6. PREVENTISTAS ---
    console.log("Capturing 6. Preventistas...");
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-preventistas"]')?.click()`);
    await wait(700);
    await capture("21_preventistas_desktop_1440_dark", 1440, 900, "dark");
    await capture("22_preventistas_mobile_390_dark", 390, 844, "dark");

    // --- 7. DASHBOARD METRICS ---
    console.log("Capturing 7. Dashboard...");
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-dashboard"]')?.click()`);
    await wait(700);
    await capture("23_dashboard_desktop_1440_dark", 1440, 900, "dark");
    await capture("24_dashboard_mobile_390_dark", 390, 844, "dark");

    // --- 8. TESORERÍA INTEGRAL ---
    console.log("Capturing 8. Tesorería...");
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-tesoreria"]')?.click()`);
    await wait(800);
    await capture("25_tesoreria_disponibilidad_desktop_1440_dark", 1440, 900, "dark");
    await capture("26_tesoreria_mobile_390_dark", 390, 844, "dark");

    // Tab Aperturas (Apertura Económica Diferida)
    await cdp.eval(`document.querySelector('[data-testid="tab-aperturas"]')?.click()`);
    await wait(600);
    await capture("27_tesoreria_aperturas_desktop_1440_dark", 1440, 900, "dark");

    // Tab Cuenta Corriente Ezequiel
    await cdp.eval(`document.querySelector('[data-testid="tab-cuenta-corriente-ezequiel"]')?.click()`);
    await wait(600);
    await capture("28_tesoreria_cc_ezequiel_desktop_1440_dark", 1440, 900, "dark");

    // Tab Cuenta Corriente David
    await cdp.eval(`document.querySelector('[data-testid="tab-cuenta-corriente-david"]')?.click()`);
    await wait(600);
    await capture("29_tesoreria_cc_david_desktop_1440_dark", 1440, 900, "dark");

    // Tab Traslados
    await cdp.eval(`document.querySelector('[data-testid="tab-traslados"]')?.click()`);
    await wait(600);
    await capture("30_tesoreria_traslados_desktop_1440_dark", 1440, 900, "dark");

    // Tab Fondeos Pendientes
    await cdp.eval(`document.querySelector('[data-testid="tab-fondeos-pendientes"]')?.click()`);
    await wait(600);
    await capture("31_tesoreria_fondeos_desktop_1440_dark", 1440, 900, "dark");

    // --- 9. RADAR OPERATIVO ---
    console.log("Capturing 9. Radar Operativo...");
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-radar"]')?.click()`);
    await wait(700);
    await capture("32_radar_desktop_1440_dark", 1440, 900, "dark");

    // --- 10. CONFIGURACIÓN ---
    console.log("Capturing 10. Configuración...");
    await cdp.eval(`document.querySelector('[data-testid="nav-btn-configuracion"]')?.click()`);
    await wait(700);
    await capture("33_configuracion_desktop_1440_dark", 1440, 900, "dark");

    // --- 11. COLA DE SINCRONIZACIÓN MODAL ---
    console.log("Capturing 11. Modal Cola de Sincronización...");
    await cdp.eval(`document.querySelector('[data-testid="btn-open-queue"]')?.click()`);
    await wait(500);
    await capture("34_modal_sync_queue_desktop_1440_dark", 1440, 900, "dark");
    await cdp.eval(`document.querySelector('[data-testid="btn-close-queue"]')?.click()`);
    await wait(400);

    console.log("All 34 audit screenshots successfully captured!");
    ws.close();
  } finally {
    chromeProc.kill();
  }
}

main().catch((err) => {
  console.error("Screenshot error:", err);
  process.exit(1);
});
