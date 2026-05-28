from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/weather-events")
def weather_events(force: bool = Query(False)):
    from app.services.weather_events import refresh_weather_events
    return refresh_weather_events(force=force)
