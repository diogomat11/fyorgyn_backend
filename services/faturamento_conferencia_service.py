"""
Serviço de consumo dos resultados das OPs do módulo ConferenciaFaturamento
do IPASGO (novo modal de faturamento do portal).

- OP17 (op17_importar_conferencia): upsert dos itens (detalheId = IdServico) em
  faturamento_lotes, com status em letra (A/P/R/I) espelhado no Integer legado
  (A→67, P→78, R→82, I→NULL). Item com DataProcedimento vazio = 'I' (Inapto).
- OP18 (op18_conferir_faturamento): aplica o resultado da confirmação/data
  (itens_sucesso) nos itens correspondentes.

A regra de desvinculação de agendamentos em mudança de data/status segue o
mesmo comportamento da branch legada da OP6 (guias_sync_service).
"""
import json
from datetime import datetime, date, timezone

from sqlalchemy.orm import Session

from models import FaturamentoLote, LoteConvenio, LoteAgendamentoItem, LoteAgendamento

# Letra do novo portal -> inteiro legado (67=Conferido, 78=Não Conferido, 82=Removido)
LETRA_PARA_INTEIRO = {"A": 67, "P": 78, "R": 82, "I": None}
INTEIRO_PARA_LETRA = {67: "A", 78: "P", 82: "R"}


def _para_data(valor):
    """Normaliza datas vindas do result_data (JSON: string ISO) para date."""
    if valor is None or isinstance(valor, date):
        return valor
    try:
        return datetime.strptime(str(valor)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _para_int(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _parse_params(job) -> dict:
    params = job.params
    if isinstance(params, str):
        try:
            return json.loads(params)
        except Exception:
            return {}
    return params or {}


def _desempacotar_resultado(res_data):
    """
    O dispatcher envelopa retornos como {status, data, job_id}; outros caminhos
    gravam o retorno cru da OP (lição do job 23632). Normaliza.
    """
    inner = res_data.get("data") if isinstance(res_data, dict) else None
    if isinstance(inner, dict) and ("itens" in inner or "itens_sucesso" in inner):
        return inner
    return res_data


def upsert_itens_conferencia(db: Session, job, res_data: dict, synced_counts: dict) -> None:
    """Consome o envelope da OP17: {protocolo, numero_lote, id_lote_interno, itens[]}."""
    if not isinstance(res_data, dict):
        print(f"[SYNC] Job {job.id} OP17: resultado inesperado ({type(res_data)}).")
        job.result_consumed = True
        synced_counts["skipped"] = synced_counts.get("skipped", 0) + 1
        return

    res_data = _desempacotar_resultado(res_data)
    itens = res_data.get("itens") or []
    p_params = _parse_params(job)
    protocolo = str(res_data.get("protocolo") or p_params.get("protocolo") or "").strip()
    numero_lote = res_data.get("numero_lote") or p_params.get("numero_lote")
    id_lote = res_data.get("id_lote_interno") or p_params.get("id_lote_interno")

    lote = db.query(LoteConvenio).filter(LoteConvenio.id_lote == id_lote).first() if id_lote else None
    numero_lote = _para_int(numero_lote)
    if not lote and numero_lote:
        lote = db.query(LoteConvenio).filter(
            LoteConvenio.numero_lote == numero_lote,
            LoteConvenio.id_convenio == 6,
        ).first()
    if not lote and protocolo:
        # Lote do portal ainda não cadastrado no hub: localiza pelo protocolo
        # (gravado na coluna desde a migration 0114) antes de criar
        lote = db.query(LoteConvenio).filter(
            LoteConvenio.protocolo == protocolo,
            LoteConvenio.id_convenio == 6,
        ).first()
    if not lote:
        # Comportamento da OP6 legada: lote do portal inexistente no hub é criado
        # automaticamente na importação (número/protocolo/datas vindos do portal)
        if not numero_lote and not protocolo:
            print(f"[SYNC] Job {job.id} OP17: sem id_lote_interno/numero_lote/protocolo — itens não persistidos.")
            job.result_consumed = True
            synced_counts["skipped"] = synced_counts.get("skipped", 0) + 1
            db.commit()
            return
        datas = [d for d in (_para_data(i.get("dataRealizacao")) for i in itens) if d]
        cod_prestador = (
            p_params.get("codigoPrestador")
            or p_params.get("cod_prestador")
            or ""
        )
        lote = LoteConvenio(
            id_convenio=6,
            numero_lote=numero_lote,
            protocolo=protocolo or None,
            cod_prestador=str(cod_prestador).strip() or None,
            status="Aberto",
            user_id=job.user_id,
            data_inicio=min(datas) if datas else None,
            data_fim=max(datas) if datas else None,
        )
        db.add(lote)
        db.flush()
        print(f"[SYNC] Job {job.id} OP17: lote {lote.numero_lote} (protocolo {lote.protocolo}) "
              f"criado automaticamente (id_lote={lote.id_lote}).")

    if numero_lote and lote.numero_lote != int(numero_lote):
        lote.numero_lote = int(numero_lote)
    if protocolo and not lote.protocolo:
        lote.protocolo = protocolo

    user_id = lote.user_id or job.user_id

    existentes = {}
    if itens:
        detalhe_ids = [i.get("detalheId") for i in itens if i.get("detalheId") is not None]
        for i in range(0, len(detalhe_ids), 900):
            chunk = detalhe_ids[i:i + 900]
            rows = db.query(FaturamentoLote).filter(FaturamentoLote.detalheId.in_(chunk)).all()
            for row in rows:
                existentes[row.detalheId] = row

    now_utc = datetime.now(timezone.utc)
    inseridos = atualizados = 0
    lotes_ag_para_reconciliar = set()

    for item in itens:
        det_id = item.get("detalheId")
        if det_id is None:
            continue

        letra = str(item.get("status_conferencia") or "P").strip().upper()
        letra = letra if letra in LETRA_PARA_INTEIRO else "P"
        inteiro = LETRA_PARA_INTEIRO[letra]
        nova_data = _para_data(item.get("dataRealizacao"))
        existente = existentes.get(det_id)

        if existente:
            # Mesma regra da OP6: data mudou ou saiu de Conferido -> desvincula agendamento
            data_mudou = str(existente.dataRealizacao) != str(nova_data)
            status_era_conferido = existente.StatusConferencia == 67 or existente.status_conferencia == "A"
            status_mudou = (existente.StatusConferencia != inteiro) or (existente.status_conferencia != letra)

            deve_desvincular = (
                existente.agendamento_id is not None and
                (data_mudou or (status_era_conferido and status_mudou))
            )
            if deve_desvincular:
                lai = db.query(LoteAgendamentoItem).filter(
                    LoteAgendamentoItem.id_faturamento_lote == existente.id
                ).first()
                if lai:
                    lotes_ag_para_reconciliar.add(lai.id_lote_ag)
                    lai.status_conciliacao = "Não Conciliado"
                    lai.id_faturamento_lote = None
                existente.agendamento_id = None
                existente.StatusConciliacao = "pendente"

            existente.dataRealizacao = nova_data
            existente.Guia = str(item.get("guia") or existente.Guia or "")
            existente.StatusConferencia = inteiro
            existente.status_conferencia = letra
            if "tem_anexo" in item and item.get("tem_anexo") is not None:
                existente.tem_anexo = bool(item.get("tem_anexo"))
            existente.ValorProcedimento = item.get("ValorProcedimento", existente.ValorProcedimento)
            existente.CodigoBeneficiario = item.get("codigo_beneficiario") or existente.CodigoBeneficiario
            existente.cod_procedimento_fat = item.get("cod_procedimento_fat") or existente.cod_procedimento_fat
            existente.id_lote = lote.id_lote
            existente.user_id = user_id
            existente.updated_at = now_utc
            atualizados += 1
        else:
            db.add(FaturamentoLote(
                detalheId=det_id,
                CodigoBeneficiario=item.get("codigo_beneficiario", ""),
                dataRealizacao=nova_data,
                Guia=str(item.get("guia") or ""),
                StatusConferencia=inteiro,
                status_conferencia=letra,
                tem_anexo=bool(item.get("tem_anexo", False)),
                ValorProcedimento=item.get("ValorProcedimento", 0.0),
                cod_procedimento_fat=item.get("cod_procedimento_fat", ""),
                id_lote=lote.id_lote,
                StatusConciliacao="pendente",
                updated_at=now_utc,
                user_id=user_id,
            ))
            inseridos += 1

    db.commit()

    if lotes_ag_para_reconciliar:
        try:
            from routes.conciliacao import process_conciliacao_bg
            for id_lote_ag in lotes_ag_para_reconciliar:
                lote_ag = db.query(LoteAgendamento).filter_by(id_lote_ag=id_lote_ag).first()
                if lote_ag and lote_ag.id_lote_convenio:
                    process_conciliacao_bg(lote_ag.id_lote_convenio, id_lote_ag, user_id)
        except Exception as e:
            print(f"[SYNC] Job {job.id} OP17: falha ao reagendar conciliação: {e}")

    inaptos = sum(1 for i in itens if str(i.get("status_conferencia") or "").upper() == "I")
    print(f"[SYNC] Job {job.id} OP17: {inseridos} itens inseridos, {atualizados} atualizados, "
          f"{inaptos} inapto(s) (lote {lote.id_lote}).")

    job.result_consumed = True
    synced_counts["jobs_processed"] = synced_counts.get("jobs_processed", 0) + 1
    db.commit()


def marcar_itens_conferidos(db: Session, job, res_data: dict, synced_counts: dict) -> None:
    """Consome o envelope da OP18: itens_sucesso assumem o status enviado (+data)."""
    if not isinstance(res_data, dict):
        job.result_consumed = True
        synced_counts["skipped"] = synced_counts.get("skipped", 0) + 1
        return

    res_data = _desempacotar_resultado(res_data)
    itens_sucesso = res_data.get("itens_sucesso") or []
    p_params = _parse_params(job)

    # Status/data por item (batch) ou status único (unitário)
    status_map = {}
    data_map = {}
    itens_param = p_params.get("itens")
    if isinstance(itens_param, list) and itens_param:
        for it in itens_param:
            chave = it.get("idServico", it.get("detalheId"))
            status_map[str(chave)] = str(it.get("status") or "")
            data_map[str(chave)] = it.get("dataRealizacao")
    status_unit = str(p_params.get("status") or "")
    data_unit = p_params.get("dataRealizacao")

    # Traduz o status do domínio do portal p/ letra
    portal_para_letra = {"Aceito": "A", "Recusado": "R", "Pendente": "P",
                         "67": "A", "82": "R", "78": "P"}

    atualizados = 0
    now_utc = datetime.now(timezone.utc)
    if itens_sucesso:
        rows = db.query(FaturamentoLote).filter(
            FaturamentoLote.detalheId.in_(itens_sucesso)
        ).all()
        for f in rows:
            status_raw = status_map.get(str(f.detalheId), status_unit)
            letra = portal_para_letra.get(status_raw.strip(), status_raw.strip().upper())
            if letra not in LETRA_PARA_INTEIRO:
                letra = "A"  # default conservador para confirmação
            f.status_conferencia = letra
            f.StatusConferencia = LETRA_PARA_INTEIRO[letra]
            data_br = data_map.get(str(f.detalheId), data_unit)
            if data_br:
                try:
                    f.dataRealizacao = datetime.strptime(str(data_br), "%d/%m/%Y").date()
                except ValueError:
                    pass
            f.updated_at = now_utc
            atualizados += 1
        db.commit()

    print(f"[SYNC] Job {job.id} OP18: {atualizados} item(ns) atualizados "
          f"({len(res_data.get('itens_erro') or [])} erro(s) no worker).")

    job.result_consumed = True
    synced_counts["jobs_processed"] = synced_counts.get("jobs_processed", 0) + 1
    db.commit()


def marcar_guia_com_anexo(db: Session, job, res_data: dict, synced_counts: dict) -> None:
    """Consome o resultado da OP19: marca tem_anexo=True nos itens da guia no lote."""
    p_params = _parse_params(job)
    inner = res_data.get("data") if isinstance(res_data, dict) and isinstance(res_data.get("data"), dict) else (res_data if isinstance(res_data, dict) else {})
    protocolo = str(inner.get("protocolo") or p_params.get("protocolo") or "").strip()
    guia = str(inner.get("guia") or p_params.get("numeroGuiaOperadora") or p_params.get("guia") or "").strip().lstrip("0")

    if guia:
        q = db.query(FaturamentoLote).filter(FaturamentoLote.Guia == guia)
        if protocolo:
            lote = db.query(LoteConvenio).filter(
                LoteConvenio.protocolo == protocolo,
                LoteConvenio.id_convenio == 6,
            ).first()
            if lote:
                q = q.filter(FaturamentoLote.id_lote == lote.id_lote)
        atualizados = q.update({FaturamentoLote.tem_anexo: True}, synchronize_session=False)
        print(f"[SYNC] Job {job.id} OP19: guia {guia} marcada com tem_anexo=True em {atualizados} item(ns).")

    job.result_consumed = True
    synced_counts["jobs_processed"] = synced_counts.get("jobs_processed", 0) + 1
    db.commit()
