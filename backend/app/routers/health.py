from fastapi import APIRouter

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
    return {
        "status": "degraded" if issues else "ok",
        "issues": issues,
    }
