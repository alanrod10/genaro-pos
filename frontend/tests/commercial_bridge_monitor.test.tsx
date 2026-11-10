import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Header } from "../src/components/Header";
import { TesoreriaLab } from "../src/components/TesoreriaLab";
import { TesoreriaService } from "../src/services/tesoreria";

vi.mock("../src/services/tesoreria", () => {
  return {
    TesoreriaService: {
      obtenerEstadoLaboratorio: vi.fn().mockResolvedValue({
        modulo: "GENARO TESORERÍA",
        fase: "SPRINT 3",
        aislamiento: "ESTRICTO_LOCAL_SQLITE",
        db_path: "/test/tesoreria_lab.db",
        conexion_remota: false,
        turso_conectado: false,
        integridad_catalogo: true,
        jornada_activa: true,
        usuario: "admin_test",
      }),
      obtenerResumen: vi.fn().mockResolvedValue({
        total_fondos: 9,
        fondos_activos: 9,
        fondos_bloqueados: 0,
        fondos_inactivos: 0,
        total_saldo_centavos: 100000,
        total_saldo_formateado: "$1.000,00",
        total_disponible_centavos: 100000,
        total_disponible_formateado: "$1.000,00",
        total_reservado_centavos: 0,
        total_reservado_formateado: "$0,00",
        jornada_activa: null,
      }),
      getTreasurySummary: vi.fn().mockResolvedValue({
        system_status: "HEALTHY",
        active_journal: null,
        totals: {
          real_balance_cents: 100000,
          real_balance_formatted: "$1.000,00",
          available_balance_cents: 100000,
          available_balance_formatted: "$1.000,00",
          reserved_balance_cents: 0,
          reserved_balance_formatted: "$0,00",
          transit_balance_cents: 0,
          transit_balance_formatted: "$0,00",
          custody_balance_cents: 0,
          custody_balance_formatted: "$0,00",
        },
        funds: [],
      }),
      obtenerCatalogo: vi.fn().mockResolvedValue([]),
      obtenerJornadaActual: vi.fn().mockResolvedValue(null),
      listarMovimientos: vi.fn().mockResolvedValue([]),
      listarTraslados: vi.fn().mockResolvedValue([]),
      listarReservas: vi.fn().mockResolvedValue([]),
      getReconciliationHistory: vi.fn().mockResolvedValue([]),
      getDifferenceCases: vi.fn().mockResolvedValue([]),
      getReversals: vi.fn().mockResolvedValue([]),
      queryAuditEvents: vi.fn().mockResolvedValue([]),
      obtenerEstadoOutbox: vi.fn().mockResolvedValue({
        total: 10, pending: 0, sent: 10, failed: 0, dead_letter: 0
      }),
      // Métodos Etapa 5
      getCommercialBridgeSyncStatus: vi.fn().mockResolvedValue({
        sincronizadas: 42,
        pendientes: 0,
        requiere_atencion: false,
        mensaje_cajero: "Todas las operaciones sincronizadas",
        timestamp: "2026-10-31T16:00:00Z",
      }),
      getCommercialBridgeStatus: vi.fn().mockResolvedValue({
        total: 15,
        pending: 2,
        processing: 1,
        published: 10,
        failed: 1,
        dead_letter: 1,
        retry_count_total: 4,
      }),
      getCommercialBridgeEvents: vi.fn().mockResolvedValue({
        total: 2,
        limit: 50,
        offset: 0,
        events: [
          {
            sequence_id: 1,
            event_id: "evt-001",
            event_type: "SALE_CONFIRMED",
            entity_type: "TICKET",
            entity_id: "TKT-100",
            action_type: "ENTRY",
            idempotency_key: "idem-tkt-100",
            payload_canonical_hash: "hash123",
            payload: { ticket_id: "TKT-100" },
            status: "PUBLISHED",
            retry_count: 0,
            max_retries: 5,
            tesoreria_movement_id: "mov-uuid-1",
            created_at: "2026-10-31T15:00:00Z",
            updated_at: "2026-10-31T15:00:01Z",
            published_at: "2026-10-31T15:00:01Z",
          },
          {
            sequence_id: 2,
            event_id: "evt-002",
            event_type: "EXPENSE_CONFIRMED",
            entity_type: "EXPENSE",
            entity_id: "EXP-200",
            action_type: "EXIT",
            idempotency_key: "idem-exp-200",
            payload_canonical_hash: "hash456",
            payload: { gasto_id: "EXP-200" },
            status: "DEAD_LETTER",
            retry_count: 5,
            max_retries: 5,
            error_message: "Saldo insuficiente en fondo 1110",
            created_at: "2026-10-31T15:10:00Z",
            updated_at: "2026-10-31T15:12:00Z",
          },
        ],
      }),
      reprocessCommercialEvent: vi.fn().mockResolvedValue({
        event_id: "evt-002",
        status: "PUBLISHED",
        tesoreria_movement_id: "mov-uuid-reprocessed",
        authorized_by: "alan",
        reprocessed_at: "2026-10-31T16:15:00Z",
        drain_stats: { published: 1, failed: 0, dead_letter: 0 },
      }),
      triggerCommercialBridgeDrain: vi.fn().mockResolvedValue({
        published: 1,
        failed: 0,
        dead_letter: 0,
        message: "Ciclo completado con éxito",
      }),
    },
  };
});

describe("ETAPA 5 — Frontend: Commercial Bridge Monitor & Sync Indicator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("1. Header renderiza el indicador de sincronización para Cajero", () => {
    render(
      <Header
        isOnline={true}
        isDemo={false}
        activeView="caja"
        onSelectView={vi.fn()}
        onToggleDemo={vi.fn()}
        pendingCount={0}
        reviewCount={0}
        onOpenQueue={vi.fn()}
        syncStatus={{
          sincronizadas: 25,
          pendientes: 0,
          requiere_atencion: false,
          mensaje_cajero: "Todas las operaciones sincronizadas",
        }}
      />
    );

    const indicator = screen.getByTestId("cashier-sync-indicator");
    expect(indicator).toBeInTheDocument();
    expect(indicator).toHaveTextContent(/Tesorería al día/i);
  });

  it("2. Header muestra advertencia clara cuando se requiere atención", () => {
    render(
      <Header
        isOnline={true}
        isDemo={false}
        activeView="caja"
        onSelectView={vi.fn()}
        onToggleDemo={vi.fn()}
        pendingCount={0}
        reviewCount={0}
        onOpenQueue={vi.fn()}
        syncStatus={{
          sincronizadas: 20,
          pendientes: 2,
          requiere_atencion: true,
          mensaje_cajero: "Atención requerida: error de saldo",
        }}
      />
    );

    const indicator = screen.getByTestId("cashier-sync-indicator");
    expect(indicator).toHaveTextContent(/Atención Sync/i);
    expect(indicator).toHaveClass("sync-pill-attention");
  });

  it("3. TesoreriaLab renderiza las métricas y la tabla del Commercial Bridge", async () => {
    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    // Cambiar a la pestaña outbox
    const tabOutbox = screen.getByTestId("tab-outbox");
    fireEvent.click(tabOutbox);

    await waitFor(() => {
      expect(screen.getByTestId("commercial-bridge-monitor")).toBeInTheDocument();
    });

    expect(screen.getByTestId("metric-total")).toHaveTextContent("15");
    expect(screen.getByTestId("metric-pending")).toHaveTextContent("2");
    expect(screen.getByTestId("metric-published")).toHaveTextContent("10");
    expect(screen.getByTestId("metric-dead-letter")).toHaveTextContent("1");

    expect(screen.getByText("SALE_CONFIRMED")).toBeInTheDocument();
    expect(screen.getByText("EXPENSE_CONFIRMED")).toBeInTheDocument();
  });

  it("4. Permite forzar sincronización inmediata y reprocesar eventos en DEAD_LETTER", async () => {
    window.confirm = vi.fn().mockReturnValue(true);

    render(<TesoreriaLab currentUser={{ username: "alan", display_name: "Alan" }} />);

    const tabOutbox = screen.getByTestId("tab-outbox");
    fireEvent.click(tabOutbox);

    await waitFor(() => {
      expect(screen.getByTestId("btn-forzar-drenaje")).toBeInTheDocument();
    });

    // Forzar sincronización
    fireEvent.click(screen.getByTestId("btn-forzar-drenaje"));
    expect(TesoreriaService.triggerCommercialBridgeDrain).toHaveBeenCalled();
    await screen.findByText("Ciclo completado con éxito");

    // Reprocesar evento evt-002
    const btnReprocesar = screen.getByTestId("btn-reprocesar-evt-002");
    expect(btnReprocesar).toBeInTheDocument();
    fireEvent.click(btnReprocesar);
    expect(TesoreriaService.reprocessCommercialEvent).toHaveBeenCalledWith("evt-002");
    await screen.findByText(/Evento evt-002 reprocesado con éxito/i);
  });
});
