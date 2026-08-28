-- ============================================================
-- Migration 0099: Remover views de retrocompatibilidade worker.convenios
-- ============================================================
-- Description:
--   A migration 0086 renomeou as tabelas worker.convenios -> worker.integradores
--   e worker.convenio_operacoes -> worker.integrador_operacoes, criando em
--   seguida VIEWS com os nomes antigos para retrocompatibilidade.
--
--   Verificado (2026-08): nenhum codigo Python consulta essas views - o ORM
--   usa WorkerIntegrador/WorkerIntegradorOperacao direto. Os aliases Python
--   WorkerConvenio/WorkerConvenioOperacao foram removidos de models.py e
--   routes/convenios.py (EP-06 do plano de ajustes 2026-08).
--
--   -> Drop seguro das views para eliminar a duplicidade percebida no banco.

DROP VIEW IF EXISTS worker.convenios;
DROP VIEW IF EXISTS worker.convenio_operacoes;
