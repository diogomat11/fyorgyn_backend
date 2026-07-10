ALTER TABLE convenios ADD COLUMN IF NOT EXISTS registro_ans TEXT;
UPDATE convenios SET registro_ans = '005711' WHERE id_convenio = 1;
