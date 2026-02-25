from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import ServerConfig
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter()

class ServerConfigBase(BaseModel):
    server_url: str
    id_convenio: Optional[int] = None
    rotina: Optional[str] = None
    preference_bonus: int = 1
    is_active: bool = True

class ServerConfigUpdate(BaseModel):
    id_convenio: Optional[int] = None
    rotina: Optional[str] = None
    preference_bonus: Optional[int] = None
    is_active: Optional[bool] = None

class ServerConfigResponse(ServerConfigBase):
    id: int
    class Config:
        from_attributes = True

@router.get("/", response_model=List[ServerConfigResponse])
def list_configs(db: Session = Depends(get_db)):
    return db.query(ServerConfig).order_by(ServerConfig.server_url).all()

@router.post("/", response_model=ServerConfigResponse)
def create_config(cfg: ServerConfigBase, db: Session = Depends(get_db)):
    existing = db.query(ServerConfig).filter(ServerConfig.server_url == cfg.server_url).first()
    if existing:
        raise HTTPException(status_code=409, detail="Config for this server_url already exists. Use PATCH to update.")
    new = ServerConfig(**cfg.model_dump())
    db.add(new)
    db.commit()
    db.refresh(new)
    return new

@router.patch("/{cfg_id}", response_model=ServerConfigResponse)
def update_config(cfg_id: int, update: ServerConfigUpdate, db: Session = Depends(get_db)):
    row = db.query(ServerConfig).filter(ServerConfig.id == cfg_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")
    for k, v in update.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row

@router.delete("/{cfg_id}")
def delete_config(cfg_id: int, db: Session = Depends(get_db)):
    row = db.query(ServerConfig).filter(ServerConfig.id == cfg_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Config not found")
    db.delete(row)
    db.commit()
    return {"message": "Deleted", "id": cfg_id}
