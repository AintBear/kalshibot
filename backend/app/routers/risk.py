from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class KillRequest(BaseModel):
    reason: str = "manual kill"


@router.get("/risk/status")
def get_risk_status():
    from app.services.risk import risk_status

    return risk_status()


@router.post("/risk/kill")
def kill(body: KillRequest):
    from app.services.risk import activate_kill_switch

    return activate_kill_switch(reason=body.reason, actor="owner")


@router.post("/risk/resume")
def resume():
    from app.services.risk import deactivate_kill_switch

    return deactivate_kill_switch(actor="owner")


@router.get("/risk/audit")
def audit_trail(limit: int = 100):
    from app.services.audit import recent

    return {"entries": recent(min(500, max(1, limit)))}
