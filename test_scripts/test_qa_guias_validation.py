"""
Script de Testes QA e Validação (PO > DEV > QA).
Valida:
1. Função localize_datetime com objetos datetime.date puros (prevenção de AttributeError 500).
2. Regras de parse de status em _normalize_status e is_authorized_status.
3. Execução da rota list_guias(aba='autorizadas') e list_guias(aba='solicitacoes').
"""
import sys
import os
from datetime import datetime, date, timezone

# Add backend directory to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from timezone_utils import localize_datetime
from services.guias_sync_service import _normalize_status, is_authorized_status
from database import SessionLocal
from models import User
from routes.guias import list_guias

def test_qa_localize_datetime():
    print("--- [QA TEST 1] Validando localize_datetime ---")
    # Test 1.1: None
    assert localize_datetime(None) is None, "Falha: None deveria retornar None"
    
    # Test 1.2: datetime.date (Não deve lançar AttributeError: 'datetime.date' object has no attribute 'tzinfo')
    d = date(2026, 7, 22)
    res_d = localize_datetime(d)
    assert res_d == d, f"Falha: date deveria ser retornado intacto, obtido {res_d}"
    
    # Test 1.3: datetime.datetime naive
    dt = datetime(2026, 7, 22, 12, 0, 0)
    res_dt = localize_datetime(dt)
    assert res_dt is not None and res_dt.tzinfo is not None, "Falha: datetime naive deveria ser localizado para SP"
    
    print("[QA TEST 1 PASSED] localize_datetime validado com sucesso para date e datetime.")

def test_qa_status_parse_rules():
    print("\n--- [QA TEST 2] Validando Regras de Parse de Status (PO > DEV > QA) ---")
    
    # 2.1 Parse de Liberado / Liberada -> Autorizada / Autorizado
    assert _normalize_status("Liberada", 2, {}) == "Autorizada", "Falha: 'Liberada' deve ser convertido para 'Autorizada'"
    assert _normalize_status("Liberado", 2, {}) == "Autorizado", "Falha: 'Liberado' deve ser convertido para 'Autorizado'"
    assert _normalize_status("Parcialmente liberada", 2, {}) == "Parcialmente autorizada", "Falha: 'Parcialmente liberada' deve converter para 'Parcialmente autorizada'"
    
    # 2.2 Termos de autorização mantidos
    assert _normalize_status("Autorizado", 2, {}) == "Autorizado"
    assert _normalize_status("Autorizada", 2, {}) == "Autorizada"
    
    # 2.3 Status de Bradesco Faturamento / Outros (Liberada / Exportada do Bradesco ID 1)
    bradesco_liberada = _normalize_status("4", 1, {}) # status 4 -> Liberada
    assert bradesco_liberada == "Liberada", "Bradesco status 4 deve retornar Liberada"
    assert is_authorized_status(bradesco_liberada, 1) is False, "Status 'Liberada' do Bradesco Faturamento NAO deve ser considerado autorizado para base_guias"
    
    # 2.4 Status autorizados verificados por is_authorized_status
    assert is_authorized_status("Autorizado", 2) is True
    assert is_authorized_status("Autorizada", 2) is True
    assert is_authorized_status("Parcialmente autorizada", 2) is True
    assert is_authorized_status("EM ESTUDO", 2) is False
    assert is_authorized_status("PENDENTE", 2) is False
    assert is_authorized_status("NEGADO", 2) is False
    
    print("[QA TEST 2 PASSED] Regras de parse de status e separacao de tabelas validadas com sucesso.")

def test_qa_list_guias_execution():
    print("\n--- [QA TEST 3] Validando Execucao do Endpoint list_guias ---")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == 1).first()
        assert user is not None, "Usuario ID 1 nao encontrado para teste"
        
        # Test 3.1: aba='autorizadas'
        res_autorizadas = list_guias(aba='autorizadas', limit=25, skip=0, db=db, current_user=user)
        assert "data" in res_autorizadas and "total" in res_autorizadas, "Resposta invalida para aba autorizadas"
        print(f"  [Aba Guias (Autorizadas)] Total: {res_autorizadas['total']}, Retornados nesta pagina: {len(res_autorizadas['data'])}")
        
        # Garantir que todas as guias retornadas tenham status com 'autorizad'
        for g in res_autorizadas['data']:
            st = str(g.get('status_guia', '')).lower()
            assert "autorizad" in st, f"Erro: Guia {g.get('guia')} possui status nao autorizado '{st}' na aba Guias!"
        
        # Test 3.2: aba='solicitacoes'
        res_solicitacoes = list_guias(aba='solicitacoes', limit=25, skip=0, db=db, current_user=user)
        assert "data" in res_solicitacoes and "total" in res_solicitacoes, "Resposta invalida para aba solicitacoes"
        print(f"  [Aba Solicitacoes] Total: {res_solicitacoes['total']}, Retornados nesta pagina: {len(res_solicitacoes['data'])}")
        
        print("[QA TEST 3 PASSED] list_guias executou com sucesso (HTTP 200 OK equivalente) sem erros 500.")
    finally:
        db.close()

if __name__ == "__main__":
    test_qa_localize_datetime()
    test_qa_status_parse_rules()
    test_qa_list_guias_execution()
