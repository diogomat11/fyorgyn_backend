import os
import asyncio
import time
from typing import Optional
from fastapi import FastAPI, Depends, Body, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import engine, Base, get_db, SessionLocal
from services.cleanup_service import delete_expired_patients, cleanup_expired_attachments


def _create_all_com_retry(tentativas: int = 5, espera_s: float = 3.0):
    """create_all na inicialização com retry — o pooler do Supabase tem janelas
    de instabilidade em que a 1ª conexão morre no handshake ('SSL connection
    has been closed unexpectedly', incidente 2026-11-21); uma única falha
    NÃO pode impedir o backend de subir. O pool descarta a conexão quebrada
    entre tentativas (pool_pre_ping + reciclagem)."""
    ultimo = None
    for i in range(1, tentativas + 1):
        try:
            Base.metadata.create_all(bind=engine)
            if i > 1:
                print(f"create_all OK na tentativa {i}/{tentativas}")
            return
        except Exception as e:
            ultimo = e
            print(f"create_all falhou (tentativa {i}/{tentativas}): {str(e)[:120]}")
            time.sleep(espera_s * i)
    raise ultimo


# Create tables (com retry — ver acima)
_create_all_com_retry()

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
async def read_root():
    return {"message": "FyorGyn API is running"}

@app.get("/health")
async def health_check():
    # Endpoint dedicado para o health check do Render (a raiz "/" ja responde tambem).
    # async de proposito: endpoints sync (def) dependem do threadpool anyio, que
    # ficou esgotado durante o incidente de 2026-09-02 e derrubou o health check
    # (503). Sem I/O aqui, responde direto no event loop mesmo com pool cheio.
    return {"status": "ok"}

def _run_cleanup_once():
    """Um ciclo do cleanup com CADA etapa isolada: erro de conexão numa etapa
    (ex.: Supavisor matou a conexão durante o purge — 'SSL connection has been
    closed unexpectedly', incidente 2026-11-21) não pode pular as seguintes.
    O rollback descarta a transação quebrada; o pool_pre_ping reabre conexão
    nova na próxima checkout."""
    db = None
    try:
        db = SessionLocal()
    except Exception as e:
        print(f"Cleanup Loop Error (checkout): {e}")
        return

    def _etapa(nome, fn):
        nonlocal db
        try:
            fn(db)
        except Exception as e:
            print(f"Cleanup Loop Error ({nome}): {e}")
            try: db.rollback()  # descarta transação/conexão quebrada
            except Exception: pass

    try:
        _etapa("pacientes", delete_expired_patients)
        _etapa("anexos", cleanup_expired_attachments)
        # Retenção de worker.jobs/logs (2026-08): pending stale (>24h sem
        # processar é apagado), finalizados e logs antigos (>30d).
        from services.retention_service import run_retention
        def _ret(db_):
            resumo = run_retention(db_)
            if any(resumo.values()):
                print(f"[RETENTION] {resumo}")
        _etapa("retention", _ret)
        # Re-checagem de solicitações pendentes (guia 25939699): enfileira
        # job de importação por tenant quando o status está stale — nunca lança.
        from services.solicitacao_reconcile_service import run_reconcile_pending_solicitacoes
        def _reconcile(db_):
            resumo_reconcile = run_reconcile_pending_solicitacoes(db_)
            if resumo_reconcile.get("jobs_criados"):
                print(f"[RECONCILE] {resumo_reconcile}")
        _etapa("reconcile", _reconcile)
        # Jobs 'processing' órfãos (incidente 2026-10-23): frota morta deixava
        # locks sem dono — o recover do dispatcher só roda com a frota viva.
        # O hub recupera quando o job está stale E o slot não heartbeata.
        from services.cleanup_service import reclaim_orphaned_processing_jobs
        def _reclaim(db_):
            resumo_reclaim = reclaim_orphaned_processing_jobs(db_)
            if resumo_reclaim.get("reclaimed_pending") or resumo_reclaim.get("reclaimed_error"):
                print(f"[RECLAIM] {resumo_reclaim}")
        _etapa("reclaim", _reclaim)
    finally:
        if db:
            try: db.close()
            except Exception: pass


async def run_cleanup_loop():
    while True:
        # DB+Storage são I/O síncrono (psycopg2/requests): rodar direto no
        # event loop congelava o servidor INTEIRO (inclusive /health) por
        # ~80s a cada ciclo (incidente 2026-11-18) — to_thread devolve o
        # loop às requisições enquanto o cleanup roda em background.
        await asyncio.to_thread(_run_cleanup_once)

        await asyncio.sleep(600)  # Run every 10 minutes

async def run_guias_sync_loop():
    """Loop de fundo (fallback do webhook) que consome jobs success não consumidos.

    Intervalo configurável (SYNC_LOOP_INTERVAL, default 15s): o webhook continua
    sendo o caminho primário; polls de 5s re-transferiam do Supabas a fila inteira
    de jobs não consumidos a cada ciclo (fonte do egress de 2026-08)."""
    sync_interval = int(os.getenv("SYNC_LOOP_INTERVAL", "15"))
    while True:
        try:
            from services.guias_sync_service import sync_completed_worker_jobs_bg
            await asyncio.to_thread(sync_completed_worker_jobs_bg)
        except Exception as e:
            # Nunca engolir silenciosamente: erros engolidos aqui deixaram a fila
            # de jobs não consumidos crescer unnoticed por dias (egress).
            print(f"Guias Sync Loop Error: {e}")

        await asyncio.sleep(sync_interval)

async def run_cron_loop():
    """Loop de segundo plano (a cada 60s) que dispara os crons de carteirinhas
    configurados em public.cron_configs (guarda ultimo_run no banco; tz-aware)."""
    while True:
        db = None
        try:
            from database import SessionLocal
            from services.cron_service import run_due_crons
            db = SessionLocal()
            await asyncio.to_thread(run_due_crons, db)
        except Exception as e:
            print(f"Cron Loop Error: {e}")
        finally:
            if db:
                try: db.close()
                except Exception: pass

        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(run_cleanup_loop())
    asyncio.create_task(run_guias_sync_loop())
    asyncio.create_task(run_cron_loop())
    # Retomada de extrações RM órfãs de crash/restart (lote Gemini interrompido
    # ficava preso em NAO_PROCESSADO/EM_PROCESSAMENTO para sempre).
    try:
        from database import SessionLocal
        from services.relatorio_rm_service import resume_stuck_extractions
        db = SessionLocal()
        try:
            # to_thread: I/O síncrono de DB não pode bloquear o event loop
            # durante o lifespan (atrasa o bind da porta e o health check).
            await asyncio.to_thread(resume_stuck_extractions, db)
        finally:
            db.close()
    except Exception as e:
        print(f"Startup resume_stuck_extractions falhou (não bloqueia o boot): {e}")

from routes import (
    auth, carteirinhas, jobs, guias, logs, dashboard, debug_optimization,
    workers, pei, convenios, prio_rules, metrics, agendamentos, server_configs,
    lotes, conciliacao, protocolo, relatorios_rm, motivos_faltas, workflows,
    unidades, crm, integradores, comprovante, workflow_trigger, crons,
    docs_rm, docs_clinicos, recurso_glosa
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
    auth_header = request.headers.get("Authorization", "")
    is_authenticated = auth_header.startswith("Bearer ")

    # Requisições autenticadas de criação de jobs/lotes possuem cota liberada para lote (10.000 req/min)
    if is_authenticated and (path.startswith("/api/jobs") or path.startswith("/api/agendamentos") or path.startswith("/api/lotes")):
        limit, window = 10000, 60
        key = f"{client_ip}:authenticated_batch"
    elif path == "/api/auth/login":
        limit, window = 15, 60  # 15 req/min para login (proteção contra força bruta)
        key = f"{client_ip}:login"
    elif path.startswith("/api/jobs") and request.method == "POST":
        limit, window = 1000, 60  # 1000 req/min
        key = f"{client_ip}:jobs_post"
    else:
        limit, window = 2000, 60 # 2000 req/min para leituras e polling geral
        key = f"{client_ip}:general"

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
app.include_router(recurso_glosa.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")
app.include_router(unidades.router, prefix="/api")
app.include_router(crm.router, prefix="/api")
app.include_router(integradores.router, prefix="/api")
app.include_router(crons.router, prefix="/api")
app.include_router(comprovante.router, prefix="/api")
app.include_router(workflow_trigger.router, prefix="/api")
app.include_router(docs_rm.router, prefix="/api/docs/rm", tags=["Docs RM"])
app.include_router(docs_clinicos.router, prefix="/api/docs/clinicos", tags=["Docs Clínicos"])

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
