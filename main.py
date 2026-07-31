from fastapi import FastAPI, Depends, Body, BackgroundTasks
from typing import Optional
# Trigger Redeploy
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from database import engine, Base, get_db
from sqlalchemy.orm import Session
from routes import auth, carteirinhas, jobs, guias, logs, dashboard, debug_optimization
import os

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="FyorGyn API", version="1.0.0")

# Monta a pasta de uploads para arquivos estáticos de forma nativa
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


# Configure CORS
origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "https://clmf-gestor.vercel.app",
    "https://clmf-hub-unimed-frontend.vercel.app"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "FyorGyn API is running"}

import asyncio
from database import SessionLocal
from services.cleanup_service import delete_expired_patients, cleanup_expired_attachments

async def run_cleanup_loop():
    while True:
        try:
            db = SessionLocal()
            delete_expired_patients(db)
            cleanup_expired_attachments(db)
            db.close()
        except Exception as e:
            print(f"Cleanup Loop Error: {e}")
        
        await asyncio.sleep(600) # Run every 10 minutes

async def run_guias_sync_loop():
    """Loop contínuo de segundo plano (a cada 5s) para consumir jobs de worker pendentes de sincronização."""
    while True:
        try:
            db = SessionLocal()
            from services.guias_sync_service import sync_completed_worker_jobs
            sync_completed_worker_jobs(db)
            db.close()
        except Exception as e:
            pass
        
        await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_cleanup_loop())
    asyncio.create_task(run_guias_sync_loop())

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
from sqlalchemy.orm import Session
from routes import auth, carteirinhas, jobs, guias, logs, dashboard, debug_optimization
import os

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="FyorGyn API", version="1.0.0")

# Monta a pasta de uploads para arquivos estáticos de forma nativa
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


# Configure CORS
origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "https://clmf-gestor.vercel.app",
    "https://clmf-hub-unimed-frontend.vercel.app"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "FyorGyn API is running"}

import asyncio
from database import SessionLocal
from services.cleanup_service import delete_expired_patients, cleanup_expired_attachments

async def run_cleanup_loop():
    while True:
        try:
            db = SessionLocal()
            delete_expired_patients(db)
            cleanup_expired_attachments(db)
            db.close()
        except Exception as e:
            print(f"Cleanup Loop Error: {e}")
        
        await asyncio.sleep(600) # Run every 10 minutes

async def run_guias_sync_loop():
    """Loop contínuo de segundo plano (a cada 5s) para consumir jobs de worker pendentes de sincronização."""
    while True:
        try:
            db = SessionLocal()
            from services.guias_sync_service import sync_completed_worker_jobs
            sync_completed_worker_jobs(db)
            db.close()
        except Exception as e:
            pass
        
        await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_cleanup_loop())
    asyncio.create_task(run_guias_sync_loop())

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(carteirinhas.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(guias.router, prefix="/api")
app.include_router(logs.router, prefix="/api/logs")
app.include_router(dashboard.router, prefix="/api")

from routes import workers, pei, convenios, prio_rules, metrics, agendamentos, server_configs, lotes, conciliacao, protocolo, relatorios_rm, motivos_faltas, workflows, unidades, crm
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
