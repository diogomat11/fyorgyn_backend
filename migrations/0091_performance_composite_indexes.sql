-- ============================================================
-- Migration 0091: Índices Compostos Multi-Tenant de Alta Performance
-- Executar no Supabase SQL Editor
-- ============================================================

-- 1. Agendamentos: ordenação padrão e paginação multi-tenant instantânea
CREATE INDEX IF NOT EXISTS idx_agendamentos_user_data_hora 
    ON agendamentos (user_id, data DESC, hora_inicio DESC);

-- 2. Agendamentos: contagem agregada de KPIs e filtros de Status por tenant
CREATE INDEX IF NOT EXISTS idx_agendamentos_user_status 
    ON agendamentos (user_id, "Status");

-- 3. Agendamentos: filtros compostos por convênio e período
CREATE INDEX IF NOT EXISTS idx_agendamentos_user_conv_data 
    ON agendamentos (user_id, id_convenio, data);

-- 4. Agendamentos: aceleração de vinculação e joins por número de guia
CREATE INDEX IF NOT EXISTS idx_agendamentos_guia_user 
    ON agendamentos (numero_guia, user_id) WHERE numero_guia IS NOT NULL AND numero_guia <> '';

-- 5. Base Guias: busca instantânea de saldo e status por guia e tenant
CREATE INDEX IF NOT EXISTS idx_base_guias_guia_user 
    ON base_guias (guia, user_id);

CREATE INDEX IF NOT EXISTS idx_base_guias_user_status_saldo 
    ON base_guias (user_id, status_guia, saldo);

-- 6. Patient PEI: KPIs de validade e filtros de dashboard
CREATE INDEX IF NOT EXISTS idx_patient_pei_user_validade 
    ON patient_pei (user_id, validade);

CREATE INDEX IF NOT EXISTS idx_patient_pei_user_status 
    ON patient_pei (user_id, status);
