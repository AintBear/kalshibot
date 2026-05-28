from fastapi import APIRouter

router = APIRouter()


@router.get("/brain/status")
def brain_status():
    from app.services.weather_brain import get_brain_status
    return get_brain_status()


@router.post("/brain/rebuild")
def rebuild_brain():
    from app.services.adaptive_policy import rebuild_snapshots
    result = rebuild_snapshots()
    return {"rebuilt": len(result), "segments": result}
