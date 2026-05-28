from fastapi import APIRouter

router = APIRouter()


@router.get("/kalshi/balance")
def kalshi_balance():
    from app import config as cfg
    from app.services.kalshi_client import get_balance

    return get_balance(cfg.load())


@router.post("/kalshi/backfill-settlements")
def kalshi_backfill_settlements():
    from app.services.kalshi_client import backfill_settlements

    return backfill_settlements()
