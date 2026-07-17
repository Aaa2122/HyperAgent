from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent.config import Settings
from agent.domain import AutomationCommand, KillSwitchCommand, KillSwitchState
from agent.scheduler import AutomationScheduler
from agent.service import AgentService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    service = AgentService(settings)

    scheduler = AutomationScheduler(service, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(
        title="Hyperliquid Agent Reliability API",
        version="0.2.0",
        description="PAPER by default, with explicitly gated Hyperliquid TESTNET/MAINNET execution.",
        lifespan=lifespan,
    )
    app.state.agent = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "mode": settings.agent_mode.value}

    @app.get("/api/dashboard")
    def dashboard() -> dict:
        data = service.dashboard()
        data["automation"] = scheduler.status()
        return data

    @app.get("/api/performance")
    def performance() -> dict:
        return service.performance()

    @app.get("/api/trades")
    def trades() -> dict:
        return service.trade_history()

    @app.get("/api/trades/metrics")
    def trade_metrics() -> dict:
        return service.trade_metrics()

    @app.get("/api/instruments")
    def instruments() -> dict:
        return service.instrument_registry()

    @app.get("/api/positions/analytics")
    def position_analytics() -> dict:
        return service.position_analytics()

    @app.post("/api/automation")
    def set_automation(command: AutomationCommand) -> dict:
        try:
            return scheduler.configure(**command.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/automation/status")
    def automation_status() -> dict:
        return scheduler.status()

    @app.get("/api/integrations/hyperliquid/readiness")
    def hyperliquid_readiness() -> dict:
        try:
            return service.hyperliquid_readiness()
        except Exception as exc:
            return {
                "network": settings.hyperliquid_execution_network,
                "configured": bool(
                    settings.hyperliquid_private_key and settings.hyperliquid_account_address
                ),
                "ready_for_orders": False,
                "blockers": ["READINESS_CHECK_FAILED"],
                "error": type(exc).__name__,
            }

    @app.post("/api/execution/reconcile")
    def reconcile_execution() -> dict:
        results = service.execution.reconcile()
        return {
            "results": [item.model_dump(mode="json") for item in results],
            "unresolved": service.repository.unresolved_intents(),
        }

    @app.post("/api/cycles/run")
    def run_cycle() -> dict:
        try:
            return service.run_cycle()
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/killswitch")
    def set_kill_switch(command: KillSwitchCommand) -> dict:
        current = service.repository.current_kill_switch()
        if current is KillSwitchState.HALTED and command.state is not KillSwitchState.HALTED:
            raise HTTPException(
                status_code=409,
                detail="HALTED requires explicit recovery and reconciliation; dashboard resume is forbidden",
            )
        result = service.repository.transition_kill_switch(
            command.state, command.reason, command.actor
        )
        result["automation"] = scheduler.on_kill_switch_changed(command.state.value)
        return result

    dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = dist / path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
