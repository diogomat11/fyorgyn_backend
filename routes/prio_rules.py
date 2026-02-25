from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import PriorityRule
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter()

class PriorityRuleBase(BaseModel):
    id_convenio: int
    rotina: str
    base_priority: int = 2          # 0 = highest priority (top of queue)
    escalation_minutes: int = 10    # minutes between each step towards priority 0
    weight_per_day: Optional[float] = None
    is_active: bool = True

class PriorityRuleUpdate(BaseModel):
    base_priority: Optional[int] = None
    escalation_minutes: Optional[int] = None
    weight_per_day: Optional[float] = None
    is_active: Optional[bool] = None

class PriorityRuleResponse(PriorityRuleBase):
    id: int
    class Config:
        from_attributes = True

@router.get("/", response_model=List[PriorityRuleResponse])
def list_rules(db: Session = Depends(get_db)):
    return db.query(PriorityRule).order_by(PriorityRule.id_convenio, PriorityRule.rotina).all()

@router.post("/", response_model=PriorityRuleResponse)
def create_rule(rule: PriorityRuleBase, db: Session = Depends(get_db)):
    new_rule = PriorityRule(**rule.model_dump())
    db.add(new_rule)
    db.commit()
    db.refresh(new_rule)
    return new_rule

@router.patch("/{rule_id}", response_model=PriorityRuleResponse)
def update_rule(rule_id: int, rule_update: PriorityRuleUpdate, db: Session = Depends(get_db)):
    db_rule = db.query(PriorityRule).filter(PriorityRule.id == rule_id).first()
    if not db_rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for k, v in rule_update.model_dump(exclude_unset=True).items():
        setattr(db_rule, k, v)
    db.commit()
    db.refresh(db_rule)
    return db_rule

@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    db_rule = db.query(PriorityRule).filter(PriorityRule.id == rule_id).first()
    if not db_rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.delete(db_rule)
    db.commit()
    return {"message": "Rule deleted", "id": rule_id}
