import zipfile
from io import BytesIO
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session
from database import get_db
from models import Agendamento, User
from dependencies import get_current_user, get_effective_user_id
from services.pdf_comprovante import generate_guia_comprovante_pdf
from pydantic import BaseModel

router = APIRouter(
    prefix="/comprovante",
    tags=["Comprovante"]
)

class GerarComprovanteRequest(BaseModel):
    agendamento_ids: List[int]

@router.post("/gerar")
def gerar_comprovante_pdf(
    req: GerarComprovanteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not req.agendamento_ids:
        raise HTTPException(status_code=400, detail="Nenhum agendamento selecionado.")

    target_uid = get_effective_user_id(current_user)

    # 1. Fetch selected agendamentos
    query_sel = db.query(Agendamento).filter(Agendamento.id_agendamento.in_(req.agendamento_ids))
    if not current_user.is_admin:
        query_sel = query_sel.filter(Agendamento.user_id == target_uid)

    initial_agendamentos = query_sel.all()
    if not initial_agendamentos:
        raise HTTPException(status_code=404, detail="Nenhum agendamento encontrado com os IDs fornecidos.")

    # 2. Expand: Even if only 1 agendamento is checked, load ALL agendamentos for the same (Guia & Data) or (Carteirinha & Data)
    guia_dates = set((ag.numero_guia, ag.data) for ag in initial_agendamentos if ag.numero_guia and ag.data)
    cart_dates = set((ag.carteirinha, ag.data) for ag in initial_agendamentos if not ag.numero_guia and ag.carteirinha and ag.data)

    expanded_query = db.query(Agendamento)
    if not current_user.is_admin:
        expanded_query = expanded_query.filter(Agendamento.user_id == target_uid)

    filters = []
    for guia, dt in guia_dates:
        filters.append((Agendamento.numero_guia == guia) & (Agendamento.data == dt))
    for cart, dt in cart_dates:
        filters.append((Agendamento.carteirinha == cart) & (Agendamento.data == dt))

    if filters:
        all_agendamentos = expanded_query.filter(or_(*filters)).order_by(Agendamento.data, Agendamento.hora_inicio).all()
    else:
        all_agendamentos = initial_agendamentos

    # 3. Group by numero_guia (or date/patient fallback)
    guias_map = {}
    for ag in all_agendamentos:
        # Validate convenio: Unimed Goiania (3) or Unimed Intercambio (21)
        if ag.id_convenio not in [3, 21]:
            raise HTTPException(
                status_code=400,
                detail=f"Impressão de comprovante presencial disponível apenas para Unimed Goiânia (3) e Unimed Intercâmbio (21). Convênio ID #{ag.id_convenio} não suportado."
            )
        guia_key = ag.numero_guia or f"guia_{ag.carteirinha}_{ag.data}"
        if guia_key not in guias_map:
            guias_map[guia_key] = []
        guias_map[guia_key].append(ag)

    # 4. Generate PDF per guide
    pdf_results = []
    for guia_key, ag_list in guias_map.items():
        pdf_buffer = generate_guia_comprovante_pdf(ag_list, db, current_user)
        pdf_results.append((guia_key, pdf_buffer))

    if len(pdf_results) == 1:
        guia_key, pdf_buffer = pdf_results[0]
        return StreamingResponse(
            pdf_buffer,
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename=comprovante_unimed_{guia_key}.pdf"}
        )

    # Multiple guides -> return ZIP
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for guia_key, pdf_buf in pdf_results:
            zip_file.writestr(f"comprovante_unimed_{guia_key}.pdf", pdf_buf.getvalue())

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=comprovantes_unimed.zip"}
    )
