from sqlalchemy import Column, Integer, String, Date, DateTime, Time, ForeignKey, Text, Float, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

class UserConvenio(Base):
    __tablename__ = "user_convenios"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"))

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(Text, nullable=False)
    api_key = Column(Text, unique=True, nullable=False)
    validade = Column(Date)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True) # Legacy default
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    convenio_rel = relationship("Convenio", secondary="user_convenios")

class Carteirinha(Base):
    __tablename__ = "carteirinhas"

    id = Column(Integer, primary_key=True, index=True)
    carteirinha = Column(Text, unique=True, nullable=False)
    paciente = Column(Text)
    id_paciente = Column(Integer, index=True)
    id_pagamento = Column(Integer, index=True)
    status = Column(Text, default="ativo")
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    is_temporary = Column(Boolean, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    jobs = relationship("Job", back_populates="carteirinha_rel", cascade="all, delete-orphan")
    guias = relationship("BaseGuia", back_populates="carteirinha_rel", cascade="all, delete-orphan")
    logs = relationship("Log", back_populates="carteirinha_rel", cascade="all, delete-orphan")
    convenio_rel = relationship("Convenio")

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="CASCADE"))
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True)
    rotina = Column(Text) # consulta_guias, autorizacao, etc.
    params = Column(Text, nullable=True) # Arbitrary JSON parameters
    status = Column(Text, nullable=False, default="pending", index=True) # success, pending, processing, error
    attempts = Column(Integer, default=0)
    priority = Column(Integer, default=0)
    locked_by = Column(Text) # Server URL
    timeout = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    carteirinha_rel = relationship("Carteirinha", back_populates="jobs")
    convenio_rel = relationship("Convenio")
    logs = relationship("Log", back_populates="job_rel", cascade="all, delete-orphan")

class BaseGuia(Base):
    __tablename__ = "base_guias"

    id = Column(Integer, primary_key=True, index=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="CASCADE"))
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="SET NULL"), nullable=True)
    guia = Column(Text)
    data_autorizacao = Column(Date)
    senha = Column(Text)
    status_guia = Column(Text, default="Autorizado")
    validade = Column(Date)
    codigo_terapia = Column(Text)
    qtde_solicitada = Column(Integer)
    sessoes_autorizadas = Column(Integer)
    saldo = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    carteirinha_rel = relationship("Carteirinha", back_populates="guias")
    convenio_rel = relationship("Convenio")

class PeiTemp(Base):
    __tablename__ = "pei_temp"

    id = Column(Integer, primary_key=True, index=True)
    base_guia_id = Column(Integer, ForeignKey("base_guias.id", ondelete="CASCADE"), unique=True)
    pei_semanal = Column(Float)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class PatientPei(Base):
    __tablename__ = "patient_pei"

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

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    carteirinha_id = Column(Integer, ForeignKey("carteirinhas.id", ondelete="Set NULL"), nullable=True)
    level = Column(Text, default="INFO") # INFO, WARN, ERROR
    message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job_rel = relationship("Job", back_populates="logs")
    carteirinha_rel = relationship("Carteirinha", back_populates="logs")

class Worker(Base):
    __tablename__ = "workers"

    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(Text, unique=True, nullable=False)
    status = Column(Text, default="offline") # idle, processing, offline, error
    last_heartbeat = Column(DateTime(timezone=True), server_default=func.now())
    current_job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
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
from services.pei_service import update_patient_pei




class Convenio(Base):
    __tablename__ = "convenios"

    id_convenio = Column(Integer, primary_key=True, index=True)
    nome = Column(Text, nullable=False)
    usuario = Column(Text)
    senha_criptografada = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    operacoes_rel = relationship("ConvenioOperacao", back_populates="convenio_rel", cascade="all, delete-orphan")

class ConvenioOperacao(Base):
    __tablename__ = "convenio_operacoes"

    id = Column(Integer, primary_key=True, index=True)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"))
    descricao = Column(Text, nullable=False)
    valor = Column(Text, nullable=False)
    
    convenio_rel = relationship("Convenio", back_populates="operacoes_rel")

# Event Listeners removed - Replaced by Database Triggers (migrations/0006)

class PriorityRule(Base):
    __tablename__ = "priority_rules"

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

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"))
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

    id_ficha = Column(Integer, primary_key=True, index=True)
    id_paciente = Column(Integer)
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

    id_tipo = Column(Integer, primary_key=True, index=True)
    tipo = Column(Text)
    id_doc_autorizacao = Column(Integer)
    id_doc_faturamento = Column(Integer)

class TipoDocumento(Base):
    __tablename__ = "tipo_documentos"

    id_tipo_doc = Column(Integer, primary_key=True, index=True)
    nome = Column(Text)
    uso = Column(Text)

class ModeloDocumento(Base):
    __tablename__ = "modelo_documentos"

    id_modelo = Column(Integer, primary_key=True, index=True)
    id_convenio = Column(Integer, ForeignKey("convenios.id_convenio", ondelete="CASCADE"))
    nome_doc = Column(Text)
    id_tipo_faturamento = Column(Integer, ForeignKey("tipo_faturamento.id_tipo"))

    convenio_rel = relationship("Convenio")
    tipo_fat_rel = relationship("TipoFaturamento")

class Procedimento(Base):
    __tablename__ = "procedimentos"

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

    id_area = Column(Integer, primary_key=True, index=True)
    nome = Column(Text, nullable=False)
    cbo = Column(Text)
    status = Column(Text, default="ativo")

class Conselho(Base):
    __tablename__ = "conselhos"

    id_conselho = Column(Integer, primary_key=True, index=True)
    nome_conselho = Column(Text, nullable=False)

class CorpoClinico(Base):
    __tablename__ = "corpo_clinico"

    id_profissional = Column(Integer, primary_key=True, index=True)
    nome = Column(Text, nullable=False)
    cpf = Column(Text)
    area = Column(Text)
    conselho = Column(Text)
    registro = Column(Text)
    UF = Column(Text)
    CBO = Column(Text)
    codigo_ipasgo = Column(Text)
    status = Column(Text, default="ativo")

class Agendamento(Base):
    __tablename__ = "agendamentos"

    id_agendamento = Column(Integer, primary_key=True, index=True)
    id_paciente = Column(Integer)
    id_unidade = Column(Integer)
    id_carteirinha = Column(Integer)
    carteirinha = Column(Text)
    Nome_Paciente = Column(Text)
    id_convenio = Column(Integer)
    nome_convenio = Column(Text)
    data = Column(Date)
    hora_inicio = Column(Time)
    sala = Column(Text)
    Id_profissional = Column(Integer)
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
