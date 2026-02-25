-- Migration: 0029_create_clinical_and_scheduling
-- Description: Create tables for Agendamentos, Corpo Clinico, Conselhos and Areas Atuacao

-- 1. Create areas_atuacao table
CREATE TABLE IF NOT EXISTS areas_atuacao (
    id_area SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    cbo TEXT,
    status TEXT DEFAULT 'ativo'
);

-- Seed areas_atuacao
INSERT INTO areas_atuacao (nome, cbo) VALUES 
    ('PSICOLOGIA', '251510'),
    ('FONOAUDIOLOGIA', '223910'),
    ('TERAPIA OCUPACIONAL', '223905'),
    ('FISIOTERAPIA', '223605'),
    ('PSICOMOTRICIDADE', '223915'),
    ('PSICOPEDAGOGIA', '239425'),
    ('MUSICOTERAPIA', '226605')
ON CONFLICT DO NOTHING;

-- 2. Create conselhos table
CREATE TABLE IF NOT EXISTS conselhos (
    id_conselho SERIAL PRIMARY KEY,
    nome_conselho TEXT NOT NULL
);

-- Seed conselhos
INSERT INTO conselhos (nome_conselho) VALUES 
    ('CRM'),
    ('CRP'),
    ('CREFITO'),
    ('CREFONO'),
    ('AGMT'),
    ('CRO')
ON CONFLICT DO NOTHING;

-- 3. Create corpo_clinico table
CREATE TABLE IF NOT EXISTS corpo_clinico (
    id_profissional SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    cpf TEXT,
    area TEXT,
    conselho TEXT,
    registro TEXT,
    "UF" TEXT,
    "CBO" TEXT,
    codigo_ipasgo TEXT,
    status TEXT DEFAULT 'ativo'
);

-- 4. Create agendamentos table
CREATE TABLE IF NOT EXISTS agendamentos (
    id_agendamento SERIAL PRIMARY KEY,
    id_paciente INTEGER,
    id_unidade INTEGER,
    id_carteirinha INTEGER,
    carteirinha TEXT,
    "Nome_Paciente" TEXT,
    id_convenio INTEGER,
    nome_convenio TEXT,
    data DATE,
    hora_inicio TIME,
    sala TEXT,
    "Id_profissional" INTEGER,
    "Nome_profissional" TEXT,
    "Tipo_atendimento" TEXT,
    id_procedimento INTEGER,
    cod_procedimento_fat TEXT,
    nome_procedimento TEXT,
    valor_procedimento DOUBLE PRECISION,
    cod_procedimento_aut TEXT,
    "Status" TEXT NOT NULL DEFAULT 'A Confirmar'
);
