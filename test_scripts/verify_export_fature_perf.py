"""Teste de performance/saída do endpoint export_fature_jobs (pós-fix N+1).

Executa a função real do endpoint com um admin mockado e mede o tempo total,
comparando com o limite de 30s do timeout do axios. Valida também que o xlsx
gerado tem as colunas esperadas e um volume plausível de linhas.
"""
import sys, time, types
from io import BytesIO

sys.path.insert(0, ".")

from database import SessionLocal
from routes.jobs import export_fature_jobs


class FakeAdmin:
    id = 1
    is_admin = True
    convenio_rel = []
    id_convenio = None


def main():
    db = SessionLocal()
    try:
        # Convênio 1 é o maior volume (~3.3k jobs rotina '1') — o pior caso real
        for id_convenio in (1, 6):
            t0 = time.time()
            resp = export_fature_jobs(id_convenio=id_convenio, db=db, current_user=FakeAdmin())
            elapsed = time.time() - t0

            body = b""
            import asyncio
            async def consume():
                chunks = []
                async for chunk in resp.body_iterator:
                    chunks.append(chunk)
                return b"".join(chunks)
            body = asyncio.run(consume())

            import pandas as pd
            df = pd.read_excel(BytesIO(body))
            print(f"convenio {id_convenio}: {elapsed:.1f}s | {len(df)} linhas | "
                  f"xlsx {len(body)/1024:.0f} KB | colunas: {list(df.columns)}")
            assert elapsed < 30, f"ainda estoura o timeout de 30s ({elapsed:.1f}s)"
            assert len(df) > 0, "planilha vazia"
            assert "Status Job" in df.columns
        print("OK: exportação dentro do timeout e com conteúdo válido")
    finally:
        db.close()


if __name__ == "__main__":
    main()
