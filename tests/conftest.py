"""
Config da suíte de testes do backend (QA-01).

Importa os módulos do backend sem conectar ao banco (engine SQLAlchemy é lazy).
Serviços são testados com sessões fake (MagicMock) — os testes cobrem a lógica
pura de negócio (enrich de params, janela do cron, split multiprestador).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
