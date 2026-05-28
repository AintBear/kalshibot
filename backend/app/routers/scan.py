from fastapi import APIRouter, BackgroundTasks, Query

router = APIRouter()


@router.post("/scan/weather")
def trigger_scan(background_tasks: BackgroundTasks):
    from app.services.scanner import scan_weather_markets
    background_tasks.add_task(scan_weather_markets)
    return {"status": "scan_started"}


@router.get("/scan/status")
def scan_status():
    from app.services.scanner import get_scan_status
    return get_scan_status()


@router.get("/scan/diagnose")
def scan_diagnose(
    series: str = Query("KXHIGHNY", min_length=2),
    limit: int = Query(1, ge=1, le=50),
):
    from app.services.scanner import diagnose_kalshi_series
    return diagnose_kalshi_series(series=series, limit=limit)
