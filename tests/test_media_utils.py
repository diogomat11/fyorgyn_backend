"""
QA — media_utils: Content-Type correto para documentos (PDF x imagens) nos
proxies de visualização /docs/rm e /docs/clinicos. O Evoluir também armazena
relatórios como .jpg/.jpeg/.png; servir como application/pdf quebrava o viewer.
"""
from media_utils import infer_media_type, inline_filename


class TestInferMediaType:

    def test_extensao_da_url_tem_prioridade(self):
        assert infer_media_type("https://x.com/arquivosusuario/abc.jpg") == "image/jpeg"
        assert infer_media_type("https://x.com/arquivosusuario/abc.JPEG") == "image/jpeg"
        assert infer_media_type("https://x.com/arquivosusuario/abc.png") == "image/png"
        assert infer_media_type("https://x.com/arquivosusuario/abc.pdf") == "application/pdf"

    def test_extensao_vence_content_type_errado_do_upstream(self):
        # storage do portal responde text/html para imagens às vezes
        assert infer_media_type("https://x.com/a.png", upstream_content_type="text/html") == "image/png"

    def test_sem_extensao_usa_content_type_servivel_do_upstream(self):
        assert infer_media_type("https://x.com/a", upstream_content_type="image/jpeg") == "image/jpeg"
        assert infer_media_type("https://x.com/a", upstream_content_type="application/pdf; charset=binary") == "application/pdf"

    def test_sem_extensao_e_content_type_nao_servivel_cai_no_fallback(self):
        assert infer_media_type("https://x.com/a", upstream_content_type="binary/octet-stream") == "application/pdf"

    def test_query_string_ignorada(self):
        assert infer_media_type("https://x.com/a.jpg?token=123&x=1") == "image/jpeg"


class TestInlineFilename:

    def test_preserva_extensao_original(self):
        assert inline_filename("rm", 42, "https://x.com/arquivosusuario/uuid.jpg") == "rm_42.jpg"
        assert inline_filename("doc", 7, "https://x.com/arquivosusuario/uuid.pdf?download=1") == "doc_7.pdf"

    def test_sem_extensao_default_pdf(self):
        assert inline_filename("rm", 42, "https://x.com/sem-ext") == "rm_42.pdf"
