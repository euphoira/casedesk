"""Local-only HTTP inbox; action parameters and account facts are server-owned."""

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from casedesk.config import Settings
from casedesk.engine import Engine
from casedesk.policy import POLICY_REVISION
from casedesk.retrieval import KnowledgeBase
from casedesk.schemas import Approval, CreateTicket, Rejection, TicketUpdate
from casedesk.store import Conflict, Denied, Store

WEB = Path(__file__).parent / "web"


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PATCH", "PUT", "DELETE"}:
            await self.app(scope, receive, send)
            return
        chunks, length = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            length += len(chunk)
            if length > 16_000:
                await JSONResponse({"detail": "Request body exceeds 16000 bytes."}, 413)(
                    scope, receive, send
                )
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        pending = True

        async def replay():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(settings: Settings | None = None, provider=None, kb=None) -> FastAPI:
    settings = settings or Settings.from_env()
    knowledge = kb or KnowledgeBase()
    store = Store(settings.data_dir / "casedesk.sqlite3")
    engine = Engine(settings, store, knowledge, provider)
    active: set[str] = set()

    @asynccontextmanager
    async def lifespan(_app):
        store.recover()
        yield

    app = FastAPI(title="CaseDesk", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.engine, app.state.knowledge = store, engine, knowledge
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
    )

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            try:
                expected, supplied = urlsplit(str(request.base_url)), urlsplit(origin or "")
            except ValueError:
                return JSONResponse({"detail": "Invalid origin."}, 403)
            if origin and (
                (expected.scheme, expected.netloc) != (supplied.scheme, supplied.netloc)
                or supplied.path
                or supplied.query
                or supplied.fragment
            ):
                return JSONResponse({"detail": "Cross-origin writes are disabled."}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing(_request, _exc):
        return JSONResponse({"detail": "Not found."}, 404)

    @app.exception_handler(Conflict)
    async def conflict(_request, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(Denied)
    async def denied(_request, exc):
        return JSONResponse({"detail": str(exc)}, 403)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/config")
    def config():
        return {
            "mode": settings.mode,
            "model": settings.model if settings.mode == "live" else "deterministic-fixtures",
            "simulation": True,
            "corpus_revision": knowledge.revision,
            "policy_revision": POLICY_REVISION,
        }

    @app.get("/api/tickets")
    def tickets():
        return store.tickets()

    @app.get("/api/contexts")
    def contexts():
        return store.contexts()

    @app.post("/api/tickets", status_code=201)
    def create_ticket(payload: CreateTicket):
        try:
            return store.create_ticket(**payload.model_dump())
        except Conflict:
            raise
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/tickets/{ticket_id}")
    def ticket(ticket_id: str):
        return store.ticket(ticket_id)

    @app.post("/api/tickets/{ticket_id}/analyze")
    async def analyze(ticket_id: str):
        if ticket_id in active:
            raise Conflict("该工单正在分析，请稍后重试。")
        if len(active) >= 2:
            raise HTTPException(429, "最多同时分析两个工单。")
        active.add(ticket_id)
        try:
            return await engine.analyze(ticket_id)
        finally:
            active.discard(ticket_id)

    @app.get("/api/tickets/{ticket_id}/analysis")
    def analysis(ticket_id: str):
        return store.latest_analysis(ticket_id)

    @app.get("/api/tickets/{ticket_id}/audit")
    def audit(ticket_id: str):
        return store.audit(ticket_id)

    @app.get("/api/search")
    def search(q: str):
        try:
            return knowledge.search(q)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.patch("/api/tickets/{ticket_id}")
    def update_ticket(ticket_id: str, payload: TicketUpdate):
        return store.update_ticket(ticket_id, payload.expected_revision, payload.status)

    @app.post("/api/proposals/{proposal_id}/approve")
    def approve(proposal_id: str, payload: Approval):
        return store.approve(
            proposal_id, payload.ticket_revision, payload.idempotency_key, knowledge
        )

    @app.post("/api/proposals/{proposal_id}/reject")
    def reject(proposal_id: str, payload: Rejection):
        return store.reject(proposal_id, payload.ticket_revision)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB, check_dir=False), name="static")
    return app
