import os
import asyncio
from typing import Optional
from fastapi import FastAPI, Depends, Body, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import engine, Base, get_db, SessionLocal
from services.cleanup_service import delete_expired_patients, cleanup_expired_attachments

# Create tables
Base.metadata.create_all(bind=engine)

# Ensure schema migrations for WorkerApiKey new columns
try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE worker.worker_api_keys ADD COLUMN IF NOT EXISTS servers JSONB;"))
        conn.execute(text("ALTER TABLE worker.worker_api_keys ADD COLUMN IF NOT EXISTS priority_rules JSONB;"))
        conn.commit()
except Exception as e:
    print(f"Migration notice: {e}")

app = FastAPI(title="FyorGyn API", version="1.0.0")

# Monta a pasta de uploads para arquivos estáticos de forma nativa
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Configure CORS
# Origins padrao (dev local + dominios Vercel de producao) + o que vier de
# CORS_ORIGINS (CSV) — permite configurar novos dominios em producao (Render)
# sem mudar codigo. CORS_ORIGIN_REGEX (opcional) cobre previews do Vercel
# (ex.: https://tiss-service-fyorgyn.*\.vercel\.app).
_default_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "https://tiss-service-fyorgyn.vercel.app",
    "https://clmf-gestor.vercel.app",
    "https://clmf-hub-unimed-frontend.vercel.app",
]
_extra_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
origins = _default_origins + [o for o in _extra_origins if o not in _default_origins]
_origin_regex = os.getenv("CORS_ORIGIN_REGEX", "").strip() or None

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.get("/")
def read_root():
    return {"message": "FyorGyn API is running"}

@app.get("/health")
def health_check():
    # Endpoint dedicado para o health check do Render (a raiz "/" ja responde tambem).
    return {"status": "ok"}

async def run_cleanup_loop():
    while True:
        db = None
        try:
            db = SessionLocal()
            delete_expired_patients(db)
            cleanup_expired_attachments(db)
        except Exception as e:
            print(f"Cleanup Loop Error: {e}")
        finally:
            if db:
                try: db.close()
                except Exception: pass
        
        await asyncio.sleep(600)  # Run every 10 minutes

async def run_guias_sync_loop():
    """Loop contínuo de segundo plano (a cada 5s) para consumir jobs de worker pendentes de sincronização."""
    while True:
        try:
            from services.guias_sync_service import sync_completed_worker_jobs_bg
            await asyncio.to_thread(sync_completed_worker_jobs_bg)
        except Exception as e:
            pass
        
        await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_cleanup_loop())
    asyncio.create_task(run_guias_sync_loop())

# Include routers
from routes import (
    auth, carteirinhas, jobs, guias, logs, dashboard, debug_optimization,
    workers, pei, convenios, prio_rules, metrics, agendamentos, server_configs,
    lotes, conciliacao, protocolo, relatorios_rm, motivos_faltas, workflows,
    unidades, crm, integradores, comprovante, workflow_trigger
)

# ── Rate Limiter Middleware ──
import time
from collections import defaultdict
from fastapi import Request
from fastapi.responses import JSONResponse

_rate_limit_records = defaultdict(list)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "127.0.0.1"
    now = time.time()
    path = request.url.path

    # Define rate limits
    if path == "/api/auth/login":
        limit, window = 10, 60  # 10 req/min for login
    elif path.startswith("/api/jobs") and request.method == "POST":
        limit, window = 60, 60  # 60 req/min for job creation
    else:
        limit, window = 300, 60 # 300 req/min general

    key = f"{client_ip}:{path if path == '/api/auth/login' else 'general'}"
    timestamps = [t for t in _rate_limit_records[key] if now - t < window]
    _rate_limit_records[key] = timestamps

    if len(timestamps) >= limit:
        return JSONResponse(
            status_code=429,
            content={"detail": "Limite de requisições excedido (Rate Limit). Aguarde um instante."}
        )

    _rate_limit_records[key].append(now)
    return await call_next(request)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(carteirinhas.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(guias.router, prefix="/api")
app.include_router(logs.router, prefix="/api/logs")
app.include_router(dashboard.router, prefix="/api")
app.include_router(workers.router, prefix="/api")
app.include_router(pei.router, prefix="/api")
app.include_router(debug_optimization.router, prefix="/api")
app.include_router(convenios.router, prefix="/api")
app.include_router(prio_rules.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")
app.include_router(agendamentos.router, prefix="/api")
app.include_router(server_configs.router, prefix="/api")
app.include_router(lotes.router, prefix="/api")
app.include_router(conciliacao.router, prefix="/api")
app.include_router(protocolo.router, prefix="/api")
app.include_router(relatorios_rm.router, prefix="/api")
app.include_router(motivos_faltas.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(unidades.router, prefix="/api")
app.include_router(crm.router, prefix="/api")
app.include_router(integradores.router, prefix="/api")
app.include_router(comprovante.router, prefix="/api")
app.include_router(workflow_trigger.router, prefix="/api")

@app.post("/api/webhook")
@app.post("/webhook")
@app.post("/api/api/webhook")
@app.post("/api/api/jobs/webhook")
@app.post("/api/api/jobs/{job_id}/result")
def global_webhook_fallback(
    background_tasks: BackgroundTasks,
    job_id: Optional[int] = None,
    payload: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Rota global de fallback para webhooks prevenindo erros 404 de qualquer worker."""
    from routes.jobs import receive_worker_webhook, submit_job_result, WebhookPayload
    j_id = job_id or payload.get("job_id") or payload.get("id")
    if j_id and ("data" in payload or "result_data" in payload) and "rotina" not in payload:
        return submit_job_result(job_id=int(j_id), background_tasks=background_tasks, payload=payload, db=db)
    else:
        try:
            w_payload = WebhookPayload(
                job_id=int(j_id or 0),
                status=payload.get("status", "success"),
                result_data=payload.get("result_data") or payload.get("data"),
                error_message=payload.get("error_message"),
                attempts=payload.get("attempts", 1),
                rotina=payload.get("rotina", "op1_importar_agendamentos"),
                id_convenio=payload.get("id_convenio"),
                params=payload.get("params")
            )
            return receive_worker_webhook(payload=w_payload, background_tasks=background_tasks, db=db)
        except Exception:
            return submit_job_result(job_id=int(j_id or 0), background_tasks=background_tasks, payload=payload, db=db)
