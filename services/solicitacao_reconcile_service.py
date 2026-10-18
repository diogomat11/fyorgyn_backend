"""Reconciliação automática de solicitações pendentes (incidente guia 25939699).

Problema: o status de uma solicitação (ex.: IPASGO 'Aguardando documentação')
só era atualizado quando um job de importação cobrindo aquela guia completava.
Como os imports são disparados manualmente, uma guia autorizada no portal
podia ficar dias exibida como pendente na aba Solicitações — o oposto
(guia autorizada na aba Guias + solicitação órfã) apareceu quando o import
chegava por janelas diferentes.

Solução: este serviço roda no loop de cleanup (10 min) e, para cada tenant
com credenciais no convênio, enfileira UM job da rotina de importação com
janela de datas cobrindo as solicitações pendentes — o mesmo efeito do
import manual que corrigiu o caso, sem depender de alguém lembrar de rodar.

Proteções contra flood de jobs no portal:
- no máximo 1 job da rotina em fila/execução por tenant (guard atômico no
  INSERT — seguro com várias instâncias do backend compartilhando o banco);
- intervalo mínimo entre re-checagens por tenant (SOLICITACAO_RECHECK_INTERVAL_HOURS,
  default 6h) medido pela última re-checagem criada;
- janela de datas limitada (SOLICITACAO_RECHECK_MAX_WINDOW_DAYS, default 92).
"""
import json
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

# Convênios com rotina de importação que atualiza status de guias/solicitações
# no portal. Estender o mapa conforme novos integradores ganharem re-checagem.
RECHECK_ROTINAS = {
    6: "op11_import_guias_api",  # IPASGO
}


def _intervalo_horas() -> int:
    try:
        return max(1, int(os.getenv("SOLICITACAO_RECHECK_INTERVAL_HOURS", "6")))
    except ValueError:
        return 6


def _janela_max_dias() -> int:
    try:
        return max(7, int(os.getenv("SOLICITACAO_RECHECK_MAX_WINDOW_DAYS", "92")))
    except ValueError:
        return 92


def _is_status_recheck(status_solicitacao) -> bool:
    """Status que ainda pode evoluir no portal (autorizado/negado/cancelado não)."""
    if not status_solicitacao:
        return True
    s = str(status_solicitacao).lower()
    for terminal in ("autorizad", "liberad", "negad", "cancelad"):
        if terminal in s:
            return False
    return True


def _grupo_stale(pendentes, agora, intervalo_h):
    """Retorna (mais_antiga_updated, menor data_solicitacao) das pendentes do grupo."""
    stale_updated = [p.updated_at for p in pendentes if p.updated_at]
    datas_sol = [p.data_solicitacao for p in pendentes if p.data_solicitacao]
    mais_antiga = min(stale_updated) if stale_updated else None
    if mais_antiga is None or mais_antiga.tzinfo is None:
        # tz-naive vindo de driver sem timezone: assume UTC para o comparativo
        mais_antiga = mais_antiga.replace(tzinfo=timezone.utc) if mais_antiga else None
    limite = agora - timedelta(hours=intervalo_h)
    if mais_antiga is None or mais_antiga < limite:
        return True, (min(datas_sol) if datas_sol else None)
    return False, None


def _monta_params(uconv, data_ini, data_fim):
    p = {
        "codigoPrestador": uconv.cod_prestador or "",
        "cod_prestador": uconv.cod_prestador or "",
        "prestador": uconv.cod_prestador or "",
        "login": uconv.login,
        "senha_criptografada": uconv.senha_criptografada,
        "data_ini": data_ini.strftime("%d/%m/%Y"),
        "data_fim": data_fim.strftime("%d/%m/%Y"),
        "origem_reconcile": True,
    }
    return p


# INSERT com guard atômico: a condição roda no mesmo comando do INSERT, então
# duas instâncias do backend (prod + local) não conseguem enfileirar em dobro.
_INSERT_RECHECK_JOB = text("""
INSERT INTO worker.jobs
    (id_convenio, rotina, params, status, priority, user_id, result_consumed,
     attempts, max_attempts, created_at, updated_at)
SELECT
    :id_convenio, :rotina, CAST(:params AS jsonb), 'pending', 5, :user_id,
    false, 0, 3, now(), now()
WHERE NOT EXISTS (
    SELECT 1 FROM worker.jobs
    WHERE rotina = :rotina
      AND user_id = :user_id
      AND id_convenio = :id_convenio
      AND status IN ('pending', 'processing')
)
RETURNING id
""")


def _ultima_recheck_do_tenant(db, user_id, id_convenio, rotina):
    row = db.execute(text("""
        SELECT max(created_at) FROM worker.jobs
        WHERE rotina = :rotina
          AND user_id = :user_id
          AND id_convenio = :id_convenio
          AND params -> 'origem_reconcile' = 'true'::jsonb
    """), {"rotina": rotina, "user_id": user_id, "id_convenio": id_convenio}).first()
    ts = row[0] if row else None
    if ts and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def run_reconcile_pending_solicitacoes(db) -> dict:
    """Enfileira jobs de re-checagem para tenants com solicitações pendentes.
    Idempotente e throttled; nunca lança (roda em loop de fundo)."""
    resumo = {"grupos_avaliados": 0, "jobs_criados": 0, "sem_credenciais": 0,
              "job_em_andamento": 0, "intervalo_curto": 0, "sem_stale": 0}
    agora = datetime.now(timezone.utc)
    intervalo_h = _intervalo_horas()
    try:
        from models import Solicitacao, UserConvenio
        grupos = (
            db.query(
                Solicitacao.user_id,
                Solicitacao.id_convenio,
            )
            .filter(Solicitacao.base_guia_id.is_(None))
            .distinct()
            .all()
        )

        for user_id, id_convenio in grupos:
            rotina = RECHECK_ROTINAS.get(id_convenio)
            if not rotina or not user_id:
                continue
            resumo["grupos_avaliados"] += 1

            pendentes = (
                db.query(Solicitacao)
                .filter(
                    Solicitacao.user_id == user_id,
                    Solicitacao.id_convenio == id_convenio,
                    Solicitacao.base_guia_id.is_(None),
                )
                .all()
            )
            pendentes = [p for p in pendentes if _is_status_recheck(p.status_solicitacao)]
            if not pendentes:
                continue

            stale, menor_data_sol = _grupo_stale(pendentes, agora, intervalo_h)
            if not stale:
                resumo["sem_stale"] += 1
                continue

            uconv = (
                db.query(UserConvenio)
                .filter(
                    UserConvenio.user_id == user_id,
                    UserConvenio.id_convenio == id_convenio,
                )
                .first()
            )
            if not uconv or not uconv.login or not uconv.senha_criptografada:
                resumo["sem_credenciais"] += 1
                continue

            ultima = _ultima_recheck_do_tenant(db, user_id, id_convenio, rotina)
            if ultima and (agora - ultima) < timedelta(hours=intervalo_h):
                resumo["intervalo_curto"] += 1
                continue

            data_fim = agora.date()
            limite_ini = data_fim - timedelta(days=_janela_max_dias())
            data_ini = (menor_data_sol - timedelta(days=1)) if menor_data_sol else limite_ini
            if data_ini < limite_ini:
                data_ini = limite_ini

            params = _monta_params(uconv, data_ini, data_fim)

            result = db.execute(_INSERT_RECHECK_JOB, {
                "id_convenio": id_convenio,
                "rotina": rotina,
                "params": json.dumps(params),
                "user_id": user_id,
            })
            criado = result.first() is not None
            db.commit()
            if criado:
                resumo["jobs_criados"] += 1
                print(
                    f"[RECONCILE] Job de re-checagem enfileirado: user={user_id} "
                    f"convenio={id_convenio} rotina={rotina} janela={params['data_ini']}..{params['data_fim']} "
                    f"({len(pendentes)} solicitações pendentes)"
                )
            else:
                resumo["job_em_andamento"] += 1
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[RECONCILE] Erro na reconciliação de solicitações: {e}")
    return resumo
