-- 0087_user_auth_profiles.sql

-- 1. Campos auth + prefixo na tabela users
ALTER TABLE users ADD COLUMN IF NOT EXISTS login TEXT UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS senha_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS perfil TEXT NOT NULL DEFAULT 'gestor';
ALTER TABLE users ADD COLUMN IF NOT EXISTS parent_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS prefixo_identificacao TEXT;

-- 2. Campos profissional executante + CNES em user_convenios (SEM prefixo)
ALTER TABLE user_convenios DROP CONSTRAINT IF EXISTS user_convenios_user_id_id_convenio_key;
ALTER TABLE user_convenios ADD COLUMN IF NOT EXISTS cnes TEXT;
ALTER TABLE user_convenios ADD COLUMN IF NOT EXISTS nome_profissional_exec TEXT;
ALTER TABLE user_convenios ADD COLUMN IF NOT EXISTS conselho_exec TEXT;
ALTER TABLE user_convenios ADD COLUMN IF NOT EXISTS numero_conselho_exec TEXT;
ALTER TABLE user_convenios ADD COLUMN IF NOT EXISTS uf_exec TEXT;
ALTER TABLE user_convenios ADD COLUMN IF NOT EXISTS cbo_exec TEXT;

-- 2b. Tabela de vinculação sub-user ↔ user_convenio
CREATE TABLE IF NOT EXISTS user_user_convenios (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  user_convenio_id INTEGER NOT NULL REFERENCES user_convenios(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_user_convenio ON user_user_convenios(user_id, user_convenio_id);

-- 3. Relação unidade ↔ cod_prestador (por convênio do user)
CREATE TABLE IF NOT EXISTS unidade_prestador (
  id SERIAL PRIMARY KEY,
  user_convenio_id INTEGER NOT NULL REFERENCES user_convenios(id) ON DELETE CASCADE,
  id_unidade INTEGER NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_unidade_prestador ON unidade_prestador(user_convenio_id, id_unidade);

-- 4. Worker keys por user
CREATE TABLE IF NOT EXISTS user_workers (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  worker_key TEXT UNIQUE NOT NULL,
  descricao TEXT,
  ativo BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Worker key no job
ALTER TABLE worker.jobs ADD COLUMN IF NOT EXISTS worker_key TEXT;

-- 6. Contador global de guia prestador
CREATE TABLE IF NOT EXISTS guia_prestador_seq (
  id SERIAL PRIMARY KEY,
  user_convenio_id INTEGER NOT NULL REFERENCES user_convenios(id) ON DELETE CASCADE,
  cod_prestador TEXT NOT NULL,
  ultimo_numero BIGINT NOT NULL DEFAULT 0,
  UNIQUE(user_convenio_id, cod_prestador)
);

-- 7. Guia lock (processamento no dispatcher)
CREATE TABLE IF NOT EXISTS worker.guia_locks (
  id SERIAL PRIMARY KEY,
  numero_guia TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  job_id INTEGER REFERENCES worker.jobs(id) ON DELETE SET NULL,
  locked_at TIMESTAMPTZ DEFAULT NOW(),
  released_at TIMESTAMPTZ,
  UNIQUE(numero_guia, user_id)
);
