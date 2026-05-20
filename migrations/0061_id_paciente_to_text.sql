-- Migration 0061: Convert id_paciente from INTEGER to TEXT
-- Reason: Some clients use UUID-style IDs (e.g. "7bef983f-627c-4fc2-914e-28cc05376ccb")
-- while others use numeric IDs (e.g. "1431"). TEXT supports both.

-- 1. carteirinhas
DROP INDEX IF EXISTS idx_carteirinhas_id_paciente;
ALTER TABLE carteirinhas
  ALTER COLUMN id_paciente TYPE TEXT USING id_paciente::TEXT;
CREATE INDEX idx_carteirinhas_id_paciente ON carteirinhas(id_paciente);

-- 2. fichas
ALTER TABLE fichas
  ALTER COLUMN id_paciente TYPE TEXT USING id_paciente::TEXT;

-- 3. agendamentos
ALTER TABLE agendamentos
  ALTER COLUMN id_paciente TYPE TEXT USING id_paciente::TEXT;

-- 4. relatorios_medicos_extracao
DROP INDEX IF EXISTS idx_relatorios_rm_paciente;
ALTER TABLE relatorios_medicos_extracao
  ALTER COLUMN id_paciente TYPE TEXT USING id_paciente::TEXT;
CREATE INDEX idx_relatorios_rm_paciente ON relatorios_medicos_extracao(id_paciente);
