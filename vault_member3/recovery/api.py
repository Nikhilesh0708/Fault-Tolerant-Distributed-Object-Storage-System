"""Run with: python -m uvicorn recovery.api:create_app --factory --port 8003"""
from contextlib import asynccontextmanager
import secrets
from typing import Literal
from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from .adapters import BackendError, Missing, Conflict, Blocked
from .config import Config
from .service import RecoveryService


class FaultRequest(BaseModel):
    node_id: str
    action: Literal["stop", "restart", "corrupt", "delete-replica", "delay", "timeout", "reject", "outdated", "full", "invalid"]
    object_id: str | None = None
    seconds: float = Field(default=0, ge=0, le=60)


class JoinRequest(BaseModel):
    node_id: str = Field(pattern=r"^node-[a-zA-Z0-9_-]{1,40}$")


def create_app(config=None, backend=None, background=True):
    config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app):
        actual = backend
        if actual is None:
            if config.mode == "demo":
                from .demo.fake_backend import FakeBackend
                actual = FakeBackend(config)
                await actual.seed()
            else:
                from .http_adapter import HttpBackend
                actual = HttpBackend(config)
        service = RecoveryService(actual, config)
        app.state.service = service
        try:
            await service.start(background=background)
            yield
        finally:
            await service.close()

    app = FastAPI(title="Vault Recovery — Member 3", version="1.0.0", lifespan=lifespan)

    def service():
        return app.state.service

    def admin(authorization: str | None = Header(default=None)):
        if config.admin_token and not secrets.compare_digest(authorization or "", "Bearer " + config.admin_token):
            raise HTTPException(401, "Valid admin bearer token required")

    @app.exception_handler(BackendError)
    async def backend_error(request, exc):
        status = 404 if isinstance(exc, Missing) else 409 if isinstance(exc, (Conflict, Blocked)) else 503
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.get("/healthz")
    async def healthz():
        return {"status": "running", "mode": config.mode, "single_worker_required": True}

    @app.get("/nodes")
    async def nodes():
        return {"nodes": [n.model_dump(mode="json") for n in service().monitor.nodes.values()]}

    @app.get("/repair-status")
    async def repair_status():
        return service().status()

    @app.get("/repair/tasks")
    async def repair_tasks(limit: int = Query(default=100, ge=1, le=500)):
        return {"tasks": [t.model_dump(mode="json") for t in list(service().repairs.tasks.values())[-limit:]]}

    @app.get("/metrics")
    async def metrics():
        return service().snapshot()

    @app.get("/events")
    async def events(limit: int = Query(default=100, ge=1, le=500)):
        return {"events": service().metrics.events(limit)}

    @app.get("/integrity-status")
    async def integrity():
        return {"summary": service().checker.summary, "problem_samples": service().checker.samples}

    @app.get("/rebalancing-status")
    async def rebalancing_status():
        reb = service().rebalancer
        return {"paused": reb.paused, "tasks": [t.model_dump() for t in reb.history]}

    @app.post("/health/check", dependencies=[Depends(admin)])
    async def health_check():
        await service().monitor.once(force=True)
        return await nodes()

    @app.post("/repair/start", dependencies=[Depends(admin)])
    async def repair_start():
        return await service().scrub()

    @app.post("/repair/{object_id}", status_code=202, dependencies=[Depends(admin)])
    async def repair_object(object_id: str):
        await service().backend.metadata(object_id)
        task, created = service().repairs.enqueue(object_id)
        if task is None:
            raise HTTPException(429, "Repair queue full; retry later")
        return {"created": created, "task": task.model_dump(mode="json")}

    @app.post("/integrity/scan", dependencies=[Depends(admin)])
    async def scan():
        return await service().scrub()

    @app.post("/rebalancing/run", dependencies=[Depends(admin)])
    async def rebalance():
        return await service().rebalancer.once()

    @app.post("/rebalancing/pause", dependencies=[Depends(admin)])
    async def pause():
        service().rebalancer.paused = True
        return {"paused": True}

    @app.post("/rebalancing/resume", dependencies=[Depends(admin)])
    async def resume():
        service().rebalancer.paused = False
        return {"paused": False}

    if config.mode == "demo":
        @app.post("/demo/faults", dependencies=[Depends(admin)])
        async def inject(request: FaultRequest):
            try:
                service().backend.inject(request.node_id, request.action, request.object_id, request.seconds)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            return {"applied": request.model_dump(), "simulation_only": True}

        @app.post("/demo/nodes", dependencies=[Depends(admin)])
        async def join(request: JoinRequest):
            service().backend.add_node(request.node_id)
            await service().monitor.once(force=True)
            return {"registered": request.node_id, "simulation_only": True}

    return app
