"""
QA-01/QA-02/QA-03 — Testes do enforcement do vínculo User↔Integrador
(DEV-02 do plano docs/plano_integradores_agendamento.md, decisão D3).

Cobrem dependencies.get_integradores_agendamento_usuario / check_integrador_habilitado
/ require_integrador_agendamento: admin vê todos, gestor vê vínculos próprios
(interseção com integradores ativos), sub-usuário herda os vínculos do gestor e
usuário sem vínculo recebe 403.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from dependencies import (
    check_integrador_habilitado,
    get_integradores_agendamento_usuario,
    require_integrador_agendamento,
)
from models import UserIntegrador, WorkerIntegrador


def make_db(mapping=None):
    mapping = mapping or {}

    def _key(q):
        # db.query(Model) recebe a classe; db.query(Model.coluna) recebe a coluna —
        # resolver a classe dona para reaproveitar o mesmo mapping.
        if isinstance(q, type):
            return q
        return getattr(q, "class_", None) or getattr(getattr(q, "parent", None), "class_", None) or q

    def query(q):
        cfg = mapping.get(_key(q), {})
        m = MagicMock()
        m.all.return_value = cfg.get("all", [])
        m.filter.return_value.all.return_value = cfg.get("all", [])
        m.filter.return_value.first.return_value = cfg.get("first")
        return m

    db = MagicMock()
    db.query.side_effect = query
    return db


def _user(is_admin=False, user_id=14, parent_user_id=None):
    return SimpleNamespace(
        id=user_id, is_admin=is_admin, parent_user_id=parent_user_id,
        perfil="gestor", status="Ativo", permissoes={}
    )


AGENDAMENTO_ATIVOS = [(100,), (101,)]


class TestGetIntegradoresAgendamentoUsuario:

    def test_admin_ve_todos_ativos(self):
        db = make_db({WorkerIntegrador: {"all": AGENDAMENTO_ATIVOS}})
        assert get_integradores_agendamento_usuario(db, _user(is_admin=True)) == [100, 101]

    def test_gestor_ve_intersecao_vinculos_x_ativos(self):
        db = make_db({
            WorkerIntegrador: {"all": AGENDAMENTO_ATIVOS},
            UserIntegrador: {"all": [(101,)]},
        })
        assert get_integradores_agendamento_usuario(db, _user()) == [101]

    def test_gestor_sem_vinculo_lista_vazia(self):
        db = make_db({
            WorkerIntegrador: {"all": AGENDAMENTO_ATIVOS},
            UserIntegrador: {"all": []},
        })
        assert get_integradores_agendamento_usuario(db, _user()) == []

    def test_vinculo_de_integrador_desativado_e_filtrado(self):
        db = make_db({
            WorkerIntegrador: {"all": [(101,)]},  # 100 saiu do ar
            UserIntegrador: {"all": [(100,), (101,)]},
        })
        assert get_integradores_agendamento_usuario(db, _user()) == [101]

    def test_subusuario_herda_vinculos_do_gestor(self):
        # sub-usuário id=99, parent=14: consulta deve usar o tenant 14
        db = make_db({
            WorkerIntegrador: {"all": AGENDAMENTO_ATIVOS},
            UserIntegrador: {"all": [(101,)]},
        })
        user = _user(user_id=99, parent_user_id=14)
        assert get_integradores_agendamento_usuario(db, user) == [101]


class TestCheckIntegradorHabilitado:

    def test_admin_passa_sem_vinculo(self):
        check_integrador_habilitado(make_db(), _user(is_admin=True), 101)  # não levanta

    def test_vinculado_passa(self):
        db = make_db({
            WorkerIntegrador: {"all": AGENDAMENTO_ATIVOS},
            UserIntegrador: {"all": [(101,)]},
        })
        check_integrador_habilitado(db, _user(), 101)  # não levanta

    def test_nao_vinculado_403_com_nome_do_integrador(self):
        db = make_db({
            WorkerIntegrador: {"all": AGENDAMENTO_ATIVOS},
            UserIntegrador: {"all": [(100,)]},
        })
        with pytest.raises(HTTPException) as exc:
            check_integrador_habilitado(db, _user(), 101)
        assert exc.value.status_code == 403
        assert "não habilitado para este usuário" in exc.value.detail


class TestRequireIntegradorAgendamento:

    def test_sem_vinculo_403(self):
        import asyncio
        import dependencies as deps
        db = make_db({
            WorkerIntegrador: {"all": AGENDAMENTO_ATIVOS},
            UserIntegrador: {"all": []},
        })
        dependency = deps.require_integrador_agendamento()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(dependency(user=_user(), db=db))
        assert exc.value.status_code == 403
        assert "Nenhum integrador de agendamento habilitado" in exc.value.detail

    def test_com_vinculo_retorna_user(self):
        import asyncio
        import dependencies as deps
        user = _user()
        db = make_db({
            WorkerIntegrador: {"all": AGENDAMENTO_ATIVOS},
            UserIntegrador: {"all": [(101,)]},
        })
        dependency = deps.require_integrador_agendamento()
        assert asyncio.run(dependency(user=user, db=db)) is user
