from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
def health():
    issues = []
    try:
        from app.database import get_conn
        conn = get_conn()
        conn.execute("SELECT 1")
        conn.close()
    except Exception:
        issues.append("database_unavailable")
    body = {
        "status": "degraded" if issues else "ok",
        "issues": issues,
    }
    status_code = 503 if issues else 200
    return JSONResponse(content=body, status_code=status_code)
