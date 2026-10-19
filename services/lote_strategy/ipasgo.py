"""
IpasgoLoteStrategy — formato NÚMERO (comportamento atual, preservado).

Usuário informa apenas data_fim + cod_prestador; o hub cria LoteConvenio
placeholder (status "Processando") e enfileira a rotina OP13 no worker, que
cria o lote no portal e atualiza numero_lote.
"""
import json as _json

from .base import LoteStrategy


class IpasgoLoteStrategy(LoteStrategy):

    tipo_formulario = "numero"
    rotina_sync = "6"  # OP6: atualização de itens do lote no portal

    def criar_lote(self, db, current_user, request) -> dict:
        from models import LoteConvenio, Job

        novo_lote = LoteConvenio(
            id_convenio=request.id_convenio,
            cod_prestador=request.cod_prestador,
            data_fim=request.data_fim,
            status="Processando",
            numero_lote=None,  # atualizado pelo worker (OP13)
            user_id=current_user.id,
        )
        db.add(novo_lote)
        db.flush()

        new_job = Job(
            id_convenio=request.id_convenio,
            rotina="13",  # OP13_criar_lote
            status="pending",
            params=_json.dumps({
                "cod_prestador": request.cod_prestador,
                "data_fim": request.data_fim.strftime("%d/%m/%Y"),
                "id_lote_interno": novo_lote.id_lote,
            }),
            user_id=current_user.id,
        )
        db.add(new_job)
        db.commit()

        return {"message": "Lote criado. Job enviado ao worker.", "id_lote": novo_lote.id_lote}
