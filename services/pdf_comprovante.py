import os
from io import BytesIO
from datetime import date
import fitz
from sqlalchemy.orm import Session
from models import Agendamento, User, UserConvenio, BaseGuia, Convenio, GuiaPrestadorSeq, PatientPei
from dependencies import get_effective_user_id

def get_next_guia_prestador_seq(db: Session, user_convenio_id: int, cod_prestador: str, prefixo: str) -> str:
    """Gera sequencial global formatado: PREFIXO - 000000001"""
    seq = db.query(GuiaPrestadorSeq).filter(
        GuiaPrestadorSeq.user_convenio_id == user_convenio_id,
        GuiaPrestadorSeq.cod_prestador == cod_prestador
    ).with_for_update().first()

    if not seq:
        seq = GuiaPrestadorSeq(user_convenio_id=user_convenio_id, cod_prestador=cod_prestador, ultimo_numero=1)
        db.add(seq)
    else:
        seq.ultimo_numero += 1
    
    db.commit()
    prefix = (prefixo or "R1").upper()
    return f"{prefix} - {seq.ultimo_numero:09d}"

def generate_guia_comprovante_pdf(agendamentos_list, db: Session, current_user: User) -> BytesIO:
    """
    Preenche o PDF modelo original 'docs/UNIMED - GUIA COMPROVANTE PRESENCIAL.pdf' 
    mantendo 100% de precisão visual e alinhamento de layout.
    """
    template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "docs", "UNIMED - GUIA COMPROVANTE PRESENCIAL.pdf")
    if not os.path.exists(template_path):
        template_path = "docs/UNIMED - GUIA COMPROVANTE PRESENCIAL.pdf"

    doc = fitz.open(template_path)
    page = doc[0]

    first_ag = agendamentos_list[0]
    num_guia = first_ag.numero_guia

    # Load BaseGuia and Convenio details
    bg = db.query(BaseGuia).filter(BaseGuia.guia == num_guia).first()
    validade_pei = "16/12/2026"
    if bg:
        pei = db.query(PatientPei).filter(PatientPei.base_guia_id == bg.id).first()
        if pei and pei.validade:
            validade_pei = pei.validade.strftime("%d/%m/%Y")
    
    conv = db.query(Convenio).filter(Convenio.id_convenio == first_ag.id_convenio).first()
    registro_ans = conv.registro_ans if (conv and conv.registro_ans) else "382876"

    # User / Tenant info
    parent_id = get_effective_user_id(current_user)
    client_user = db.query(User).filter(User.id == parent_id).first()
    client_name = client_user.username if client_user else current_user.username

    # UserConvenio info
    uc = db.query(UserConvenio).filter(
        UserConvenio.user_id == parent_id,
        UserConvenio.id_convenio == first_ag.id_convenio
    ).first()

    cod_prestador = first_ag.cod_prestador or (uc.cod_prestador if uc else "2209525")
    cnes = (uc.cnes if uc and uc.cnes else "7564910")
    nome_prof = (uc.nome_profissional_exec if uc and uc.nome_profissional_exec else "LARISSA MARTINS FERREIRA")
    conselho = (uc.conselho_exec if uc and uc.conselho_exec else "09")
    num_conselho = (uc.numero_conselho_exec if uc and uc.numero_conselho_exec else "007983")
    uf_conselho = (uc.uf_exec if uc and uc.uf_exec else "GO")
    cbo = (uc.cbo_exec if uc and uc.cbo_exec else "251510")

    prefixo = current_user.prefixo_identificacao or "R1"
    guia_prestador_str = get_next_guia_prestador_seq(db, uc.id if uc else 1, cod_prestador, prefixo)

    # Procedimento description
    proc_desc = first_ag.nome_procedimento or "PSICOLOGIA - TERAPIAS PEDIATRICAS ESPECIAIS"
    cod_fat = first_ag.cod_procedimento_fat or "2250005103"
    topo_str = f"{cod_fat} - {proc_desc}"

    font_name = "helv"
    font_size = 8.0

    # 1. Header (Nº Guia no Prestador na mesma linha do rótulo; Procedimento em Y=65)
    page.insert_text(fitz.Point(730, 48), guia_prestador_str, fontsize=8.5, fontname='helv', color=(0,0,0))
    page.insert_text(fitz.Point(335, 65), topo_str[:55], fontsize=8, fontname='helv', color=(0,0,0))
    
    # Validade PEI (posicionado à direita acima dos Dados do Contratado)
    page.insert_text(fitz.Point(620, 85), f"Validade PEI: {validade_pei}", fontsize=8.5, fontname='helv', color=(0,0,0))

    # 2. Row 1 (Código na Operadora [cod_prestador], Nome do Contratado, CNES)
    page.insert_text(fitz.Point(40, 134), str(cod_prestador), fontsize=font_size, fontname=font_name)
    page.insert_text(fitz.Point(215, 134), client_name.upper()[:45], fontsize=font_size, fontname=font_name)
    page.insert_text(fitz.Point(728, 134), str(cnes), fontsize=font_size, fontname=font_name)

    # 3. Row 2 (Nome Profissional, Conselho, Número, UF, CBO)
    page.insert_text(fitz.Point(40, 161), nome_prof.upper()[:45], fontsize=font_size, fontname=font_name)
    page.insert_text(fitz.Point(430, 161), str(conselho), fontsize=font_size, fontname=font_name)
    page.insert_text(fitz.Point(512, 161), str(num_conselho), fontsize=font_size, fontname=font_name)
    page.insert_text(fitz.Point(685, 161), str(uf_conselho), fontsize=font_size, fontname=font_name)
    page.insert_text(fitz.Point(728, 161), str(cbo), fontsize=font_size, fontname=font_name)

    # 4. Table rows (Inicia em Y=204 para alinhar com a linha divisa inferior da primeira linha)
    start_y = 204.0
    row_h = 15.6
    for idx, ag in enumerate(agendamentos_list[:22]):
        y = start_y + idx * row_h
        if ag.carteirinha:
            page.insert_text(fitz.Point(150, y), str(ag.carteirinha), fontsize=7.5, fontname=font_name)
        if ag.Nome_Paciente:
            page.insert_text(fitz.Point(315, y), str(ag.Nome_Paciente).upper()[:35], fontsize=7.5, fontname=font_name)
        if ag.numero_guia:
            page.insert_text(fitz.Point(518, y), str(ag.numero_guia), fontsize=7.5, fontname=font_name)

    # 5. Footer (Data & Assinatura)
    today_str = date.today().strftime("%d/%m/%Y")
    page.insert_text(fitz.Point(40, 565), today_str, fontsize=font_size, fontname=font_name)
    page.insert_text(fitz.Point(135, 565), nome_prof.upper()[:45], fontsize=font_size, fontname=font_name)

    buffer = BytesIO()
    pdf_bytes = doc.write()
    buffer.write(pdf_bytes)
    buffer.seek(0)
    doc.close()
    return buffer
