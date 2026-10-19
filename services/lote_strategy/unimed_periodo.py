"""
UnimedPeriodoLoteStrategy — formato PERÍODO (Unimed Goiânia e Intercâmbio).

- Lote definido por data_inicio + data_fim (ex.: janela mensal 18→17).
- Ao criar o lote, a OP4 é disparada com o filtro = exatamente o período do
  lote (fim do hardcode 01/01/2026 de routes/lotes.py).
- TODOS os itens retornados pelo filtro são vinculados ao lote, mesmo com
  dataRealizacao anterior ao início (sessão de 15/09 num lote 18/09→17/10
  permanece no lote). O upsert (_upsert_op4_guias) não filtra por data.
- Convênios com lote_periodo_tipo='mensal_fixo' auto-derivam a janela que
  contém a data de referência; 'livre' exige data_inicio explícita.
"""
from datetime import date

from fastapi import HTTPException

from .base import LoteStrategy


def _safe_date(y: int, m: int, d: int) -> date:
    while d > 28:
        try:
            return date(y, m, d)
        except ValueError:
            d -= 1
    return date(y, m, d)


def _month_shift(y: int, m: int, delta: int):
    t = (m - 1) + delta
    return y + t // 12, t % 12 + 1


def calcular_auto_periodo(dia_inicio: int, dia_fim: int, ref: date):
    """Janela [ini, fim] do ciclo que contém ref (ex. 18→17). None se inválido."""
    if not dia_inicio or not dia_fim:
        return None
    for delta in (0, -1, -2):
        y, m = _month_shift(ref.year, ref.month, delta)
        ini = _safe_date(y, m, dia_inicio)
        if dia_fim >= dia_inicio:
            fim = _safe_date(y, m, dia_fim)
        else:
            y2, m2 = _month_shift(y, m, 1)
            fim = _safe_date(y2, m2, dia_fim)
        if ini <= ref <= fim:
            return ini, fim
    return None


def validar_intervalo_em_lote_unico(db, id_convenio: int, user_id: int,
                                     data_ini: date, data_fim: date):
    """
    Regra (2026-10-18): job OP4 deve permear APENAS UM lote.

    - Nenhum lote ativo intercepta o intervalo → 400 (criar lote que contenha
      o período);
    - Intervalo intercepta mais de um lote → 400 (intervalo inválido);
    - Intercepta um lote mas extrapola os limites → 400 (ajustar ao lote).

    Sucesso: retorna o LoteConvenio correspondente para o backend vincular
    automaticamente params.id_lote ao job.
    """
    from fastapi import HTTPException
    from models import LoteConvenio

    def _fmt(d):
        return d.strftime("%d/%m/%Y") if d else "?"

    candidatos = db.query(LoteConvenio).filter(
        LoteConvenio.id_convenio == id_convenio,
        LoteConvenio.user_id == user_id,
        LoteConvenio.status != "Cancelado",
        LoteConvenio.data_inicio.isnot(None),
        LoteConvenio.data_fim.isnot(None),
        LoteConvenio.data_inicio <= data_fim,
        LoteConvenio.data_fim >= data_ini,
    ).all()

    if not candidatos:
        raise HTTPException(
            status_code=400,
            detail=(f"Nenhum lote ativo corresponde ao intervalo "
                    f"{_fmt(data_ini)} a {_fmt(data_fim)}. Crie primeiro um lote "
                    f"que contenha esse período.")
        )
    if len(candidatos) > 1:
        lista = "; ".join(
            f"Nº {l.id_lote} ({_fmt(l.data_inicio)} a {_fmt(l.data_fim)})"
            for l in candidatos
        )
        raise HTTPException(
            status_code=400,
            detail=(f"Intervalo desejado é inválido: permeia mais de um lote "
                    f"({lista}). O intervalo deve permear apenas um lote.")
        )

    lote = candidatos[0]
    if data_ini < lote.data_inicio or data_fim > lote.data_fim:
        raise HTTPException(
            status_code=400,
            detail=(f"Intervalo extrapola os limites do lote Nº {lote.id_lote} "
                    f"({_fmt(lote.data_inicio)} a {_fmt(lote.data_fim)}). "
                    f"Ajuste o intervalo para dentro do lote.")
        )
    return lote


class UnimedPeriodoLoteStrategy(LoteStrategy):

    tipo_formulario = "periodo"

    def _convenio(self, db, id_convenio):
        from models import Convenio
        convenio = db.query(Convenio).filter(Convenio.id_convenio == id_convenio).first()
        if not convenio:
            raise HTTPException(status_code=404, detail="Convênio não encontrado.")
        return convenio

    def criar_lote(self, db, current_user, request) -> dict:
        from models import LoteConvenio

        convenio = self._convenio(db, request.id_convenio)
        data_fim = request.data_fim
        data_inicio = getattr(request, "data_inicio", None)

        if not data_inicio:
            if convenio.lote_periodo_tipo == "mensal_fixo":
                janela = calcular_auto_periodo(convenio.lote_dia_inicio, convenio.lote_dia_fim, data_fim)
                if not janela:
                    raise HTTPException(
                        status_code=400,
                        detail="Configuração de período mensal fixo inválida para este convênio."
                    )
                data_inicio = janela[0]
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Informe a data de início do lote (período)."
                )

        if data_inicio > data_fim:
            raise HTTPException(status_code=400, detail="Data de início do lote é posterior à data fim.")

        novo_lote = LoteConvenio(
            id_convenio=request.id_convenio,
            cod_prestador=request.cod_prestador,
            data_inicio=data_inicio,
            data_fim=data_fim,
            status="Aberto",
            numero_lote=None,  # lote Unimed é identificado por id_lote + período
            user_id=current_user.id,
        )
        db.add(novo_lote)
        db.commit()

        # Decisão (2026-10-18): a criação do lote NÃO dispara job automático.
        # O job OP4 nasce pelo ícone de sincronização do lote (intervalo = período
        # do lote) ou pelo módulo Importações — ambos validados a permear apenas
        # um lote (validar_intervalo_em_lote_unico).
        return {
            "message": f"Lote criado (período {data_inicio.strftime('%d/%m/%Y')} a "
                       f"{data_fim.strftime('%d/%m/%Y')}). Use a sincronização do lote "
                       f"ou Importações para importar as guias.",
            "id_lote": novo_lote.id_lote,
        }

    def form_config(self, db, convenio) -> dict:
        cfg = super().form_config(db, convenio)
        if convenio.lote_periodo_tipo == "mensal_fixo":
            hoje = date.today()
            janela = calcular_auto_periodo(convenio.lote_dia_inicio, convenio.lote_dia_fim, hoje)
            if janela:
                cfg["auto_periodo"] = {
                    "data_ini": janela[0].strftime("%d/%m/%Y"),
                    "data_fim": janela[1].strftime("%d/%m/%Y"),
                }
        return cfg
