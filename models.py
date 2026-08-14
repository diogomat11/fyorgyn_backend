from sqlalchemy import Column, Integer, String, Date, DateTime, Time, ForeignKey, Text, Float, Boolean, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

class UserConvenio(Base):
    __tablename__ = "user_convenios"
    __table_args__ = {'extend_existing': True}
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"))
    # Credenciais específicas do usuário para este convênio
    login = Column(Text, nullable=True)
    senha_criptografada = Column(Text, nullable=True)
    cod_prestador = Column(Text, nullable=True)
    # Credenciais para portal de faturamento (quando diferente do portal de autorização)
    login_fat = Column(Text, nullable=True)
    senha_fat_criptografada = Column(Text, nullable=True)
    url_portal_fat = Column(Text, nullable=True)
    worker_id_convenio = Column(Integer, nullable=True)
    auto_confirmar = Column(Boolean, default=False)
    auto_executar = Column(Boolean, default=False)
    auto_faturar = Column(Boolean, default=False)
    # Profissional executante + CNES
    cnes = Column(Text, nullable=True)
    nome_profissional_exec = Column(Text, nullable=True)
    conselho_exec = Column(Text, nullable=True)
    numero_conselho_exec = Column(Text, nullable=True)
    uf_exec = Column(Text, nullable=True)
    cbo_exec = Column(Text, nullable=True)

class User(Base):
    __tablename__ = "users"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    username = Column(Text, nullable=False)
    api_key = Column(Text, unique=True, nullable=False)
    validade = Column(Date)
    status = Column(Text, nullable=False, default="Ativo")  # Ativo, Inativo
    is_admin = Column(Boolean, default=False)  # Admins see all data
    permitir_protocolo = Column(Boolean, default=False)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True) # Legacy default
    login = Column(Text, unique=True, nullable=True)
    senha_hash = Column(Text, nullable=True)
    perfil = Column(Text, nullable=False, default="gestor") # admin, gestor, supervisor, faturamento, agendamento
    parent_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    prefixo_identificacao = Column(Text, nullable=True)
    permissoes = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    convenio_rel = relationship(lambda: Convenio, secondary="user_convenios")
    user_convenios_rel = relationship("UserConvenio", foreign_keys=[UserConvenio.user_id], cascade="all, delete-orphan", overlaps="convenio_rel")

class Carteirinha(Base):
    __tablename__ = "carteirinhas"
    __table_args__ = (
        UniqueConstraint('carteirinha', 'user_id', name='uq_carteirinha_user_id'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True, index=True)
    carteirinha = Column(Text, nullable=True)
    paciente = Column(Text, index=True)
    id_paciente = Column(Text, index=True)
    codigo_beneficiario = Column(Text, nullable=True) # ID of user in external system (e.g., IPASGO)
    status = Column(Text, default="ativo")
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    is_temporary = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    cid = Column(Text, nullable=True)  # CID do diagnóstico principal do paciente (usado na autorização IPASGO)

    jobs = relationship("Job", primaryjoin="Carteirinha.id == Job.carteirinha_id", back_populates="carteirinha_rel", cascade="all, delete-orphan")
    guias = relationship("BaseGuia", back_populates="carteirinha_rel", cascade="all, delete-orphan")
    logs = relationship("Log", primaryjoin="Carteirinha.id == Log.carteirinha_id", back_populates="carteirinha_rel", cascade="all, delete-orphan")
    convenio_rel = relationship(lambda: Convenio)

class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = {'schema': 'worker', 'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="CASCADE"), nullable=True)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    rotina = Column(Text) # consulta_guias, autorizacao, etc.
    params = Column(JSONB, nullable=True) # Arbitrary JSON parameters
    result_data = Column(JSONB, nullable=True) # Resposta JSON do worker
    result_consumed = Column(Boolean, default=False) # Flag: backend já consumiu o resultado?
    status = Column(Text, nullable=False, default="pending", index=True) # success, pending, processing, error
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    priority = Column(Integer, default=0)
    depending_id = Column(Integer, ForeignKey("worker.jobs.id", ondelete="SET NULL"), nullable=True)
    locked_by = Column(Text) # Server URL
    error_message = Column(Text, nullable=True) # Última mensagem de erro
    worker_key = Column(Text, nullable=True)
    timeout = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    carteirinha_rel = relationship("Carteirinha", primaryjoin="Job.carteirinha_id == Carteirinha.id", back_populates="jobs")
    convenio_rel = relationship(lambda: Convenio)
    logs = relationship("Log", back_populates="job_rel", cascade="all, delete-orphan")
    logs = relationship("Log", back_populates="job_rel", cascade="all, delete-orphan")

class BaseGuia(Base):
    __tablename__ = "base_guias"
    __table_args__ = {'extend_existing': True}
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    id = Column(Integer, primary_key=True, index=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="CASCADE"))
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True)
    cod_prestador = Column(Text, nullable=True)
    codigo_beneficiario = Column(Text, nullable=True) # Used for link resolution in IPASGO trigger
    guia = Column(Text)
    guia_prestador = Column(Text, nullable=True)
    data_solicitacao = Column(Date, nullable=True)
    data_autorizacao = Column(Date)
    senha = Column(Text)
    status_guia = Column(Text, default="Autorizado")
    validade = Column(Date)
    codigo_terapia = Column(Text)
    nome_terapia = Column(Text, nullable=True) # Auto-resolved from procedimentos by Trigger
    qtde_solicitada = Column(Integer)
    sessoes_autorizadas = Column(Integer)
    sessoes_realizadas = Column(Integer)
    saldo = Column(Integer, default=0, nullable=False)
    timestamp_captura = Column(DateTime(timezone=True), nullable=True)
    # JSON {tipo_json, guias} resultado da validacao de vinculo do prestador
    # (Unimed Goiania via getErrosSapia). NULL quando nao validado.
    valida_prestador = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    carteirinha_rel = relationship("Carteirinha", back_populates="guias")
    convenio_rel = relationship(lambda: Convenio)

class Solicitacao(Base):
    __tablename__ = "solicitacoes"
    __table_args__ = (
        UniqueConstraint('guia', 'id_convenio', 'codigo_terapia', 'carteirinha_id', 'user_id', name='uq_solicitacao_guia_terapia'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="CASCADE"))
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True)
    
    # Dados da guia
    guia = Column(Text)
    codigo_terapia = Column(Text)
    nome_terapia = Column(Text, nullable=True)
    qtde_solicitada = Column(Integer, default=0)
    sessoes_autorizadas = Column(Integer, default=0)
    data_solicitacao = Column(Date, nullable=True)
    data_autorizacao = Column(Date, nullable=True)
    senha = Column(Text, nullable=True)
    validade = Column(Date, nullable=True)
    status_solicitacao = Column(Text, default="Pendente")
    
    # Dados do formulário
    id_profissional = Column(Text, nullable=True)
    id_medico = Column(Text, nullable=True)
    observacao = Column(Text, nullable=True)
    paciente_CID = Column("paciente_cid", Text, nullable=True)
    
    # Anexos
    anexo_RM = Column("anexo_rm", Text, nullable=True)
    anexo_AI = Column("anexo_ai", Text, nullable=True)
    anexo_RC = Column("anexo_rc", Text, nullable=True)
    
    # Relacionamentos
    job_id = Column(Integer, ForeignKey("worker.jobs.id", ondelete="SET NULL"), nullable=True)
    base_guia_id = Column(Integer, ForeignKey("base_guias.id", ondelete="SET NULL"), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    carteirinha_rel = relationship("Carteirinha")
    convenio_rel = relationship("Convenio")
    job_rel = relationship("Job")
    base_guia_rel = relationship("BaseGuia")

class PeiTemp(Base):
    __tablename__ = "pei_temp"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    base_guia_id = Column(Integer, ForeignKey("base_guias.id", ondelete="CASCADE"), unique=True)
    pei_semanal = Column(Float)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class PatientPei(Base):
    __tablename__ = "patient_pei"
    __table_args__ = {'extend_existing': True}
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    id = Column(Integer, primary_key=True, index=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="CASCADE"))
    codigo_terapia = Column(Text)
    
    base_guia_id = Column(Integer, ForeignKey("base_guias.id", ondelete="CASCADE"))
    
    pei_semanal = Column(Float)
    validade = Column(Date)
    status = Column(Text) # Validated, Pendente
    
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    carteirinha_rel = relationship("Carteirinha")
    base_guia_rel = relationship("BaseGuia")


class Log(Base):
    __tablename__ = "logs"
    __table_args__ = {'schema': 'worker', 'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("worker.jobs.id", ondelete="SET NULL"), nullable=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="SET NULL"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    level = Column(Text, default="INFO") # INFO, WARN, ERROR
    message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job_rel = relationship("Job", back_populates="logs")
    carteirinha_rel = relationship("Carteirinha", primaryjoin="Log.carteirinha_id == Carteirinha.id", back_populates="logs")


class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = {'schema': 'worker', 'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(Text, unique=True, nullable=False)
    status = Column(Text, default="offline") # idle, processing, offline, error
    last_heartbeat = Column(DateTime(timezone=True), server_default=func.now())
    current_job_id = Column(Integer, ForeignKey("worker.jobs.id", ondelete="SET NULL"), nullable=True)
    command = Column(Text, nullable=True) # restart, stop, etc.
    meta = Column(Text, nullable=True) # JSON string for CPU, RAM, Version
    first_error_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    current_job = relationship("Job")

# Update relationships in Job and Carteirinha (monkey-patching or manual update below)
# We need to add 'logs' relationship to Job and Carteirinha classes above.
# Ideally I should have edited the classes. I will use a second tool call or try to match nicely.
# Actually I can't easily monkeypatch via replace inside the file text easily if I don't touch the classes.
# I will rewrite the file segments for Job and Carteirinha to include 'logs = relationship(...)'


# Event Listeners for Automatic PEI Calculation
from sqlalchemy import event
from sqlalchemy.orm import Session




class Convenio(Base):
    __tablename__ = "convenios"
    __table_args__ = {'extend_existing': True}

    id_convenio = Column(Integer, primary_key=True, index=True)
    nome = Column(Text, nullable=False)
    id_integrador = Column(Integer, nullable=True) # ID do integrador associado no schema worker
    operacoes_habilitadas = Column(JSONB, default=list, nullable=True) # Lista de rotinas/operações ativas para este convênio
    digitos_carteirinha = Column(Integer, nullable=True)
    biometria = Column(Boolean, default=False)
    timeout_captura = Column(Boolean, default=False)
    pei_automatico = Column(Boolean, default=False)
    registro_ans = Column(Text, nullable=True)
    modo_execucao = Column(Text, default="automatico") # 'automatico' ou 'manual'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    operacoes_rel = relationship("ConvenioOperacao", back_populates="convenio_rel", cascade="all, delete-orphan")

class ConvenioOperacao(Base):
    __tablename__ = "convenio_operacoes"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"))
    descricao = Column(Text, nullable=False)
    valor = Column(Text, nullable=False)
    
    convenio_rel = relationship("Convenio", back_populates="operacoes_rel")

# Event Listeners removed - Replaced by Database Triggers (migrations/0006)

class PriorityRule(Base):
    __tablename__ = "priority_rules"
    __table_args__ = {'schema': 'worker', 'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"))
    rotina = Column(Text)
    base_priority = Column(Integer, default=2)  # Starting priority level (0 = highest)
    escalation_minutes = Column(Integer, default=10)  # Minutes per priority step-up towards 0
    weight_per_day = Column(Text)  # Legacy field kept for backward compat
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    convenio_rel = relationship("Convenio")


class ServerConfig(Base):
    """
    Soft-preference rules for worker servers.
    The dispatcher gives a bonus to a server when it receives a job matching
    its preferred (id_convenio, rotina), maximising Chrome session reuse.
    """
    __tablename__ = "server_configs"
    __table_args__ = {'schema': 'worker', 'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    server_url = Column(Text, unique=True, nullable=False)  # e.g. "http://127.0.0.1:9000"
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True)
    rotina = Column(Text, nullable=True)  # NULL = any rotina for preferred convenio
    preference_bonus = Column(Integer, default=1)  # points subtracted from effective_priority for matching jobs
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    convenio_rel = relationship("Convenio", foreign_keys=[id_convenio])

class JobExecution(Base):
    __tablename__ = "job_executions"
    __table_args__ = {'schema': 'worker', 'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("worker.jobs.id", ondelete="CASCADE"))
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True)
    rotina = Column(Text)
    status = Column(Text)
    start_time = Column(DateTime(timezone=True), server_default=func.now())
    end_time = Column(DateTime(timezone=True))
    duration_seconds = Column(Integer)
    items_found = Column(Integer, default=0)
    error_category = Column(Text)
    error_message = Column(Text)
    
    from sqlalchemy.dialects.postgresql import JSONB
    meta = Column(JSONB)

    job_rel = relationship("Job")
    convenio_rel = relationship("Convenio")

class Ficha(Base):
    __tablename__ = "fichas"
    __table_args__ = {'extend_existing': True}

    id_ficha = Column(Integer, primary_key=True, index=True)
    id_paciente = Column(Text)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"))
    id_procedimento = Column(Integer, ForeignKey("procedimentos.id_procedimento"))
    id_guia = Column(Integer, ForeignKey("base_guias.id"))
    status_assinatura = Column(Text)
    status_conciliacao = Column(Text)

    convenio_rel = relationship("Convenio")
    procedimento_rel = relationship("Procedimento")
    guia_rel = relationship("BaseGuia")

class TipoFaturamento(Base):
    __tablename__ = "tipo_faturamento"
    __table_args__ = {'extend_existing': True}

    id_tipo = Column(Integer, primary_key=True, index=True)
    tipo = Column(Text)
    id_doc_autorizacao = Column(Integer)
    id_doc_faturamento = Column(Integer)

class TipoDocumento(Base):
    __tablename__ = "tipo_documentos"
    __table_args__ = {'extend_existing': True}

    id_tipo_doc = Column(Integer, primary_key=True, index=True)
    nome = Column(Text)
    uso = Column(Text)

class ModeloDocumento(Base):
    __tablename__ = "modelo_documentos"
    __table_args__ = {'extend_existing': True}

    id_modelo = Column(Integer, primary_key=True, index=True)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"))
    nome_doc = Column(Text)
    id_tipo_faturamento = Column(Integer, ForeignKey("tipo_faturamento.id_tipo"))

    convenio_rel = relationship("Convenio")
    tipo_fat_rel = relationship("TipoFaturamento")

class Procedimento(Base):
    __tablename__ = "procedimentos"
    __table_args__ = {'extend_existing': True}

    id_procedimento = Column(Integer, primary_key=True, index=True)
    nome = Column(Text)
    codigo_procedimento = Column(Text)
    faturamento = Column(Text)
    status = Column(Text, default="ativo")
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True)
    id_area = Column(Integer, ForeignKey("areas_atuacao.id_area", ondelete="SET NULL"), nullable=True)

    convenio_rel = relationship("Convenio")
    area_rel = relationship("AreaAtuacao")

class ProcedimentoFaturamento(Base):
    __tablename__ = "procedimento_faturamento"
    __table_args__ = {'extend_existing': True}

    id_proc_fat = Column(Integer, primary_key=True, index=True)
    id_procedimento = Column(Integer, ForeignKey("procedimentos.id_procedimento", ondelete="CASCADE"))
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"))
    valor = Column(Float)
    data_inicio = Column(Date)
    data_fim = Column(Date)
    status = Column(Text, default="ativo")

    procedimento_rel = relationship("Procedimento")
    convenio_rel = relationship("Convenio")

class AreaAtuacao(Base):
    __tablename__ = "areas_atuacao"
    __table_args__ = {'extend_existing': True}

    id_area = Column(Integer, primary_key=True, index=True)
    nome = Column(Text, nullable=False)
    cbo = Column(Text)
    status = Column(Text, default="ativo")

class Conselho(Base):
    __tablename__ = "conselhos"
    __table_args__ = {'extend_existing': True}

    id_conselho = Column(Integer, primary_key=True, index=True)
    nome_conselho = Column(Text, nullable=False)

class CorpoClinico(Base):
    __tablename__ = "corpo_clinico"
    __table_args__ = (
        UniqueConstraint("id_profissional", "area", name="corpo_clinico_id_prof_area_key"),
        {'extend_existing': True},
    )
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    id_profissional = Column(Text, index=True)  # medicos importados via CRM ficam NULL
    nome = Column(Text, nullable=False)
    cpf = Column(Text)
    area = Column(Text, default='')
    conselho = Column(Text)
    registro = Column(Text)
    UF = Column(Text)
    CBO = Column(Text)
    codigo_ipasgo = Column(Text)
    status = Column(Text, default="ativo")
    tipo_profissional = Column(Text, default="profissional")
    # CRM-specific (v3 API Consulta CRM Medico)
    situacao = Column(Text)  # situacao no conselho (ativo/inativo/cancelado) em lowercase
    atualizado_crm = Column(DateTime(timezone=True))  # timestamp da ultima consulta CFM

class Agendamento(Base):
    __tablename__ = "agendamentos"
    __table_args__ = {'extend_existing': True}
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    id_agendamento = Column(Integer, primary_key=True, index=True)
    id_paciente = Column(Text)
    id_unidade = Column(Integer)
    id_carteirinha = Column(Integer)
    carteirinha = Column(Text)
    Nome_Paciente = Column(Text)
    id_convenio = Column(Integer)
    nome_convenio = Column(Text)
    cod_prestador = Column(Text, nullable=True)
    data = Column(Date)
    hora_inicio = Column(Time)
    sala = Column(Text)
    Id_profissional = Column(Text)
    Nome_profissional = Column(Text)
    Tipo_atendimento = Column(Text)
    id_procedimento = Column(Integer)
    cod_procedimento_fat = Column(Text)
    nome_procedimento = Column(Text)
    valor_procedimento = Column(Float)
    cod_procedimento_aut = Column(Text)
    numero_guia = Column(Text, nullable=True)
    data_update = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    Status = Column(Text, nullable=False, default="A Confirmar")
    execucao_status = Column(Text, default="pendente")

class FaturamentoLote(Base):
    __tablename__ = "faturamento_lotes"
    __table_args__ = {'extend_existing': True}
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    id = Column(Integer, primary_key=True, index=True)
    id_lote = Column(Integer, ForeignKey("lotes_convenio.id_lote", ondelete="SET NULL"), index=True)
    detalheId = Column(Integer, unique=True, index=True, nullable=False)
    CodigoBeneficiario = Column(Text)
    StatusConciliacao = Column(Text, default="pendente")
    dataRealizacao = Column(Date)
    Guia = Column(Text)
    StatusConferencia = Column(Integer)
    ValorProcedimento = Column(Float)
    cod_procedimento_fat = Column(Text, nullable=True)
    agendamento_id = Column(Integer, ForeignKey("agendamentos.id_agendamento", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class LoteConvenio(Base):
    __tablename__ = "lotes_convenio"
    __table_args__ = {'extend_existing': True}

    id_lote = Column(Integer, primary_key=True, index=True)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    numero_lote = Column(Integer, index=True)
    cod_prestador = Column(Text)
    status = Column(Text, default="Aberto") # Aberto, Enviado, Cancelado
    data_inicio = Column(Date, nullable=True)
    data_fim = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    convenio_rel = relationship("Convenio")

class LoteAgendamento(Base):
    __tablename__ = "lotes_agendamento"
    __table_args__ = {'extend_existing': True}

    id_lote_ag = Column(Integer, primary_key=True, index=True)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    id_lote_convenio = Column(Integer, ForeignKey("lotes_convenio.id_lote", ondelete="SET NULL"), nullable=True, index=True)
    data_inicio = Column(Date)
    data_fim = Column(Date)
    status = Column(Text, default="Aberto")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    convenio_rel = relationship("Convenio")

class LoteAgendamentoItem(Base):
    __tablename__ = "lote_agendamento_itens"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    id_lote_ag = Column(Integer, ForeignKey("lotes_agendamento.id_lote_ag", ondelete="CASCADE"), index=True)
    id_agendamento = Column(Integer, ForeignKey("agendamentos.id_agendamento", ondelete="CASCADE"), index=True)
    status_conciliacao = Column(Text, default="Não Conciliado")
    id_faturamento_lote = Column(Integer, ForeignKey("faturamento_lotes.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ProtocoloLote(Base):
    """Batch (lote) of PDF files for extraction processing."""
    __tablename__ = "protocolo_lotes"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    convenio = Column(Text, nullable=True, default="unimed_goiania")
    status = Column(Text, nullable=False, default="pending", index=True)  # pending, processing, completed, error
    total_arquivos = Column(Integer, default=0)
    total_processado = Column(Integer, default=0)
    total_erro = Column(Integer, default=0)
    total_sucesso = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    arquivos = relationship("ProtocoloArquivo", back_populates="lote_rel", cascade="all, delete-orphan")


class ProtocoloArquivo(Base):
    """Individual PDF file within a processing batch."""
    __tablename__ = "protocolo_arquivos"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    lote_id = Column(Integer, ForeignKey("protocolo_lotes.id", ondelete="CASCADE"), nullable=False, index=True)
    nome_original = Column(Text, nullable=False)
    nome_final = Column(Text)
    status = Column(Text, nullable=False, default="pendente", index=True)  # pendente, processando, sucesso, erro, revisao
    tamanho_bytes = Column(Integer, default=0)

    # Extracted data from Gemini
    numero_guia_prestador = Column(Text)
    nome_beneficiario = Column(Text)
    numero_guia_principal = Column(Text)
    atendimentos = Column(JSON, nullable=True)  # [{data, assinatura}, ...]

    # Post-processing data
    guia_normalizada = Column(Text)
    erro_mensagem = Column(Text)
    gemini_model_used = Column(Text)
    gemini_api_key_index = Column(Integer)
    carteira = Column(Text, nullable=True)
    gravado = Column(Boolean, default=False, nullable=False)

    # Physical file paths
    caminho_original = Column(Text)
    caminho_final = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    lote_rel = relationship("ProtocoloLote", back_populates="arquivos")


class ProtocoloItem(Base):
    __tablename__ = "protocolo_itens"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"), nullable=True, index=True)
    cod_prestador = Column(Text, nullable=True)
    guia = Column(Text, nullable=True)
    nome = Column(Text, nullable=True)
    carteira = Column(Text, nullable=True)
    senha = Column(Text, nullable=True)
    data = Column(Date, nullable=True)
    assinatura = Column(Text, nullable=True)
    guia_prestador = Column(Text, nullable=True)
    lote_id = Column(Integer, ForeignKey("protocolo_lotes.id", ondelete="CASCADE"), nullable=True, index=True)
    arquivo_id = Column(Integer, ForeignKey("protocolo_arquivos.id", ondelete="CASCADE"), nullable=True, index=True)
    base_guia_id = Column(Integer, ForeignKey("base_guias.id", ondelete="SET NULL"), nullable=True, index=True)
    caminho_arquivo = Column(Text, nullable=True)
    faturamento_lote_id = Column(Integer, ForeignKey("faturamento_lotes.id", ondelete="SET NULL"), nullable=True, index=True)
    agendamento_id = Column(Integer, ForeignKey("agendamentos.id_agendamento", ondelete="SET NULL"), nullable=True, index=True)
    status_conciliacao = Column(Text, default="Não Conciliado", nullable=False, index=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # relationships
    lote_rel = relationship("ProtocoloLote")
    arquivo_rel = relationship("ProtocoloArquivo")
    base_guia_rel = relationship("BaseGuia")
    faturamento_rel = relationship("FaturamentoLote")
    agendamento_rel = relationship("Agendamento")



class RelatorioMedicoExtracao(Base):
    __tablename__ = "relatorios_medicos_extracao"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    id_paciente = Column(Text, nullable=False, index=True)
    nome_paciente = Column(Text)
    id_relatorio = Column(Text)
    url_arquivo = Column(Text)

    # Cargas horárias por área terapêutica
    carga_psicologia = Column(Integer)
    carga_fisioterapia = Column(Integer)
    carga_terapia_ocupacional = Column(Integer)
    carga_psicopedagogia = Column(Integer)
    carga_fonoaudiologia = Column(Integer)
    carga_psicomotricidade = Column(Integer)
    carga_musicoterapia = Column(Integer)
    carga_avaliacao_neuropsicologica = Column(Integer)

    # Metadados da extração
    tipo_carga_horaria = Column(String(20))
    status_extracao = Column(String(20), nullable=False, default="NAO_EXTRAIDO", index=True)
    itens_ignorados = Column(JSON)
    data_relatorio = Column(Date, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RelatorioClinico(Base):
    __tablename__ = "relatorios_clinicos"
    __table_args__ = (
        UniqueConstraint('id_paciente', 'id_relatorio', 'tipo_relatorio', name='uq_relatorio_clinico_pac_rel_tipo'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    id_paciente = Column(Text, index=True)
    nome_paciente = Column(Text)
    tipo_relatorio = Column(Text, nullable=False) # 'PTS' ou 'ANEXO-II'
    id_relatorio = Column(Text)
    url_arquivo = Column(Text)
    carga = Column(Text)
    tipo_carga_horaria = Column(Text)
    id_area = Column(Integer)
    data = Column(Date)
    nome_profissional = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Integrador(Base):
    __tablename__ = "integradores"
    __table_args__ = {'schema': 'public', 'extend_existing': True}

    id_integrador = Column(Integer, primary_key=True, index=True)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"), unique=True, nullable=False)
    nome = Column(Text, nullable=False)
    sigla = Column(Text, nullable=True)
    tipo_operacao = Column(Text, nullable=False, default="convenio")  # 'convenio' ou 'agendamento'
    ativo = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    convenio_rel = relationship(Convenio)
    operacoes_rel = relationship("IntegradorOperacao", back_populates="integrador_rel", cascade="all, delete-orphan")


class IntegradorOperacao(Base):
    __tablename__ = "integrador_operacoes"
    __table_args__ = {'schema': 'public', 'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    id_integrador = Column(Integer, ForeignKey("public.integradores.id_integrador", ondelete="CASCADE"), nullable=False)
    id_integrador_worker = Column(Integer, nullable=True) # Referência lógica ao worker schema sem FK cruzada
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"), nullable=False)
    rotina = Column(Text, nullable=False)
    descricao = Column(Text, nullable=True)
    tipo_processamento = Column(Text, nullable=False, default="local")  # 'local', 'server', 'remoto'
    ativo = Column(Boolean, default=True)
    ordem = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    integrador_rel = relationship(Integrador, back_populates="operacoes_rel")


class UserIntegrador(Base):
    """Integradores habilitados pelo Admin para um Client (User Gestor)."""
    __tablename__ = "user_integradores"
    __table_args__ = (
        UniqueConstraint('user_id', 'id_integrador', name='uq_user_integrador'),
        {'schema': 'public', 'extend_existing': True}
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    id_integrador = Column(Integer, ForeignKey("public.integradores.id_integrador", ondelete="CASCADE"), nullable=False)
    ativo = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user_rel = relationship("User")
    integrador_rel = relationship("Integrador")



class WorkerIntegrador(Base):
    __tablename__ = "integradores"
    __table_args__ = {'schema': 'worker', 'extend_existing': True}

    id_integrador = Column(Integer, primary_key=True, index=True)
    nome = Column(Text, nullable=False)
    sigla = Column(Text, nullable=True)
    tipo_operacao = Column(Text, default="convenio")
    ativo = Column(Boolean, default=True)
    timeout_captura = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkerIntegradorOperacao(Base):
    __tablename__ = "integrador_operacoes"
    __table_args__ = {'schema': 'worker', 'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    id_integrador = Column(Integer, ForeignKey("worker.integradores.id_integrador", ondelete="CASCADE"), nullable=False)
    rotina = Column(Text, nullable=False)
    descricao = Column(Text, nullable=True)
    tipo_processamento = Column(Text, default="local")
    ativo = Column(Boolean, default=True)
    modo_execucao = Column(Text, default="automatico") # 'automatico' ou 'manual'
    params_schema = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    integrador_rel = relationship(WorkerIntegrador, primaryjoin="WorkerIntegradorOperacao.id_integrador == WorkerIntegrador.id_integrador")


# Aliases para retrocompatibilidade
WorkerConvenio = WorkerIntegrador
WorkerConvenioOperacao = WorkerIntegradorOperacao


class WorkerConfig(Base):
    __tablename__ = "worker_config"
    __table_args__ = {'schema': 'worker', 'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    chave = Column(Text, unique=True, nullable=False)
    valor = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkerApiKey(Base):
    __tablename__ = "worker_api_keys"
    __table_args__ = {'schema': 'worker', 'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    api_key = Column(Text, unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tipo_processamento = Column(Text, nullable=False, default="local")
    tipo_operacao = Column(Text, nullable=True, default="convenio")  # 'convenio' ou 'agendamento'
    servers = Column(JSONB, nullable=True)  # ex: [{"server_num": 1, "tipo_operacao": "convenio"}, {"server_num": 2, "tipo_operacao": "agendamento"}]
    max_servers = Column(Integer, default=1)  # Scaling individual do worker (instâncias)
    dispatch_stagger_seconds = Column(Integer, default=15)  # Stagger individual do worker
    id_convenio_preferencial = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True)
    rotina_preferencial = Column(Text, nullable=True)
    preference_bonus = Column(Integer, default=1)
    base_priority = Column(Integer, default=2)
    escalation_minutes = Column(Integer, default=10)
    priority_rules = Column(JSONB, nullable=True)  # ex: lista de múltiplas regras [{id_convenio_preferencial, rotina_preferencial, preference_bonus, base_priority, escalation_minutes}]
    descricao = Column(Text, nullable=True)
    ativo = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    convenio_preferencial_rel = relationship("Convenio", foreign_keys=[id_convenio_preferencial])

class MotivoFalta(Base):
    __tablename__ = "motivos_faltas"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    descricao = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    id_mapeado = Column(Integer, nullable=True)
    status = Column(Text, default="Ativo")
    tipo = Column(Text, nullable=True)
    anexo = Column(Text, default="NÃO")


class UserConvenioWorkflow(Base):
    __tablename__ = "user_convenio_workflows"
    __table_args__ = (
        UniqueConstraint('user_id', 'id_convenio', name='uq_user_convenio_workflow'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"), nullable=False)
    nome_workflow = Column(Text, nullable=False)
    fluxo_passos = Column(JSONB, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Unidade(Base):
    __tablename__ = "unidades"
    __table_args__ = (
        UniqueConstraint('id_unidade', 'user_id', name='uq_unidade_user_id'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True, index=True)
    id_unidade = Column(Integer, nullable=False)
    nome = Column(String(255), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(20), default="Ativo")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserUserConvenio(Base):
    __tablename__ = "user_user_convenios"
    __table_args__ = (
        UniqueConstraint('user_id', 'user_convenio_id', name='uq_user_user_convenio'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_convenio_id = Column(Integer, ForeignKey("user_convenios.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UnidadePrestador(Base):
    __tablename__ = "unidade_prestador"
    __table_args__ = (
        UniqueConstraint('user_convenio_id', 'id_unidade', name='uq_unidade_prestador'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True, index=True)
    user_convenio_id = Column(Integer, ForeignKey("user_convenios.id", ondelete="CASCADE"), nullable=False)
    id_unidade = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserWorker(Base):
    __tablename__ = "user_workers"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    worker_key = Column(Text, unique=True, nullable=False)
    descricao = Column(Text, nullable=True)
    ativo = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GuiaPrestadorSeq(Base):
    __tablename__ = "guia_prestador_seq"
    __table_args__ = (
        UniqueConstraint('user_convenio_id', 'cod_prestador', name='uq_guia_prestador_seq'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True, index=True)
    user_convenio_id = Column(Integer, ForeignKey("user_convenios.id", ondelete="CASCADE"), nullable=False)
    cod_prestador = Column(Text, nullable=False)
    ultimo_numero = Column(Integer, default=0, nullable=False)


class GuiaLock(Base):
    __tablename__ = "guia_locks"
    __table_args__ = (
        UniqueConstraint('numero_guia', 'user_id', name='uq_guia_lock_active'),
        {'schema': 'worker', 'extend_existing': True}
    )

    id = Column(Integer, primary_key=True, index=True)
    numero_guia = Column(Text, nullable=False)
    user_id = Column(Integer, nullable=False)
    job_id = Column(Integer, ForeignKey("worker.jobs.id", ondelete="SET NULL"), nullable=True)
    locked_at = Column(DateTime(timezone=True), server_default=func.now())
    released_at = Column(DateTime(timezone=True), nullable=True)



