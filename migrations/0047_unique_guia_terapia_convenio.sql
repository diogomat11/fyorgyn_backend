-- Migration 47: Adicionar restrição de unicidade composta (guia + codigo_terapia + id_convenio)
-- Garante que não exista mais de uma base_guia com a mesma combinação de
-- guia + codigo_terapia + id_convenio, evitando duplicatas nas extrações OP11/OP3.

-- Passo 1: Limpar duplicatas existentes (manter o registro mais recente)
DELETE FROM base_guias a
USING base_guias b
WHERE a.id < b.id
  AND a.guia = b.guia
  AND COALESCE(a.codigo_terapia, '') = COALESCE(b.codigo_terapia, '')
  AND COALESCE(a.id_convenio, 0) = COALESCE(b.id_convenio, 0);

-- Passo 2: Criar índice UNIQUE composto (usa COALESCE para tratar NULLs como valor vazio)
-- NULLs em UNIQUE constraints do Postgres são considerados sempre distintos,
-- então usamos um índice funcional para normalizar.
CREATE UNIQUE INDEX IF NOT EXISTS uix_base_guias_guia_terapia_convenio
ON base_guias (
    guia,
    COALESCE(codigo_terapia, ''),
    COALESCE(id_convenio, 0)
);
