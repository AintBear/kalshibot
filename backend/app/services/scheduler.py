"""
Scheduler: background jobs for market scanning, trade lifecycle checks, and stale alert cleanup.
Stale alert cleanup expires old or closed-market alerts, not high-edge price extremes.
Paper trades NEVER count toward circuit breaker or loss limits.
"""
import logging
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler = None


def _scan_interval_minutes() -> int:
    try:
        from app import config as cfg
        return max(5, int(cfg.load().get("scan_interval_minutes") or 15))
    except Exception:
        return 15


def _automation_enabled() -> bool:
    try:
        from app import config as cfg
        return bool(cfg.load().get("automation_enabled", False))
    except Exception as e:
        logger.error("_automation_enabled: config load failed, disabling automation: %s", e)
        return False


def start_scheduler():
    global _scheduler
    executors = {
        "default": ThreadPoolExecutor(max_workers=4),
    }
    _scheduler = BackgroundScheduler(executors=executors)

    _scheduler.add_job(
        _startup_job,
        trigger=DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(seconds=3)),
        id="startup_automation",
        replace_existing=True,
        misfire_grace_time=600,
    )
    _scheduler.add_job(
        _scan_job,
        trigger=IntervalTrigger(minutes=_scan_interval_minutes()),
        id="weather_scan",
        replace_existing=True,
        misfire_grace_time=600,
        max_instances=1,
    )
    _scheduler.add_job(
        _lifecycle_job,
        trigger=IntervalTrigger(minutes=5),
        id="trade_lifecycle",
        replace_existing=True,
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _stop_loss_job,
        trigger=IntervalTrigger(minutes=5),
        id="paper_risk_exits",
        replace_existing=True,
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _stale_alert_cleanup,
        trigger=IntervalTrigger(minutes=5),
        id="stale_cleanup",
        replace_existing=True,
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _stale_data_cleanup_job,
        trigger=IntervalTrigger(minutes=30),
        id="stale_data_cleanup",
        replace_existing=True,
        misfire_grace_time=600,
    )
    _scheduler.add_job(
        _weather_events_job,
        trigger=IntervalTrigger(minutes=30),
        id="weather_events_refresh",
        replace_existing=True,
        misfire_grace_time=600,
    )
    _scheduler.add_job(
        _order_monitor_job,
        # 1-minute cadence: live working orders must re-quote and live SL/TP
        # must fire promptly. Every sub-task no-ops in paper mode.
        trigger=IntervalTrigger(minutes=1),
        id="order_monitor",
        replace_existing=True,
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _learning_refresh_job,
        trigger=IntervalTrigger(minutes=10),
        id="learning_refresh",
        replace_existing=True,
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _auto_entry_job,
        trigger=IntervalTrigger(minutes=5),
        id="auto_entry",
        replace_existing=True,
        misfire_grace_time=300,
        max_instances=1,
    )
    _scheduler.start()
    logger.info("Scheduler started with thread pool (max_workers=4)")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def _scan_job():
    if not _automation_enabled():
        logger.info("Scheduled scan skipped: automation_enabled=false")
        return {"skipped": True, "reason": "automation disabled"}
    try:
        from app.services.scanner import scan_weather_markets
        result = scan_weather_markets()
        logger.info("Scheduled scan complete: %s", result)
        return result
    except Exception as e:
        logger.error("Scan job error: %s", e)
        return


def _startup_job():
    """Bring a restarted backend back to current automated state."""
    try:
        from app.services.trade_lifecycle import settle_expired_open_trades
        result = settle_expired_open_trades()
        if result.get("settled", 0) > 0:
            logger.info("Startup: settled %d expired trades", result["settled"])
    except Exception as e:
        logger.error("Startup settlement error: %s", e)
    try:
        from app.services.weather_model import rebuild_isotonic_calibration
        cal_result = rebuild_isotonic_calibration()
        logger.info("Startup: isotonic calibration rebuild: %s", cal_result)
    except Exception as e:
        logger.error("Startup isotonic calibration error: %s", e)
    try:
        from app.services.weather_model import update_model_calibration
        slice_result = update_model_calibration()
        logger.info("Startup: slice calibration rebuild: %s", slice_result)
    except Exception as e:
        logger.error("Startup slice calibration error: %s", e)
    _learning_refresh_job()
    _auto_entry_job()
    if not _automation_enabled():
        return {"skipped": True, "reason": "automation disabled"}
    if not _scan_stale():
        return {"skipped": True, "reason": "recent scan already current"}
    return _scan_job()


def _scan_stale() -> bool:
    try:
        from app import config as cfg
        from app.services.scanner import get_scan_status
        interval = max(5, int(cfg.load().get("scan_interval_minutes") or 15))
        status = get_scan_status()
        if status.get("status") == "running":
            return False
        completed_at = status.get("completed_at")
        if not completed_at:
            return True
        completed = _parse_time(completed_at)
        if completed is None:
            return True
        return datetime.now(timezone.utc) - completed >= timedelta(minutes=interval)
    except Exception:
        return True


def _parse_time(value):
    try:
        raw = str(value).replace(" ", "T")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        elif "+" not in raw and raw.count("-") >= 2:
            raw = raw + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _auto_entry_job():
    try:
        from app import config as cfg
        settings = cfg.load()
        if settings.get("auto_paper_trade_enabled") or settings.get("auto_trade_enabled"):
            from app.services.auto_entry import run_automation_cycle
            cycle_result = run_automation_cycle(settings_override=settings)
            if cycle_result.get("total_entered", 0) > 0:
                logger.info("Automation cycle: %d trades opened", cycle_result["total_entered"])
    except Exception as e:
        logger.error("Auto-entry job error: %s", e)


def _learning_refresh_job():
    try:
        from app.services.trade_lifecycle import backfill_settlements, backfill_settlement_cross_reference
        from app.services.adaptive_policy import rebuild_snapshots
        backfill_result = backfill_settlements()
        cross_ref_result = backfill_settlement_cross_reference()
        rebuild_result = rebuild_snapshots()
        cal_result = {}
        try:
            from app.services.weather_model import rebuild_isotonic_calibration
            cal_result = rebuild_isotonic_calibration()
        except Exception as cal_exc:
            logger.warning("Isotonic calibration rebuild failed: %s", cal_exc)
        slice_cal_result = {}
        try:
            from app.services.weather_model import update_model_calibration
            slice_cal_result = update_model_calibration()
        except Exception as slice_exc:
            logger.warning("Slice calibration rebuild failed: %s", slice_exc)
        logger.info(
            "Learning refresh complete: backfill=%s cross_ref=%s rebuilt_segments=%d isotonic=%s slice=%s",
            backfill_result,
            cross_ref_result,
            len(rebuild_result),
            cal_result,
            slice_cal_result,
        )
        return {
            "backfill": backfill_result,
            "cross_reference": cross_ref_result,
            "rebuilt": len(rebuild_result),
            "calibration": cal_result,
            "slice_calibration": slice_cal_result,
        }
    except Exception as e:
        logger.error("Learning refresh error: %s", e)
        return {"error": str(e)}


def _weather_events_job():
    try:
        from app.services.weather_events import refresh_weather_events
        result = refresh_weather_events(force=True)
        event_count = sum(len(v) for v in (result.get("cities") or {}).values())
        logger.info("Weather events refreshed: %d active city events", event_count)
        return result
    except Exception as e:
        logger.error("Weather events refresh error: %s", e)
        return {"error": str(e)}


_ORDER_MONITOR_TICKS = 0


def _order_monitor_job():
    global _ORDER_MONITOR_TICKS
    try:
        from app.services.order_manager import (
            monitor_live_orders,
            manage_working_orders,
            reconcile_with_kalshi,
        )
        from app.services.trade_lifecycle import (
            settle_expired_open_trades,
            check_live_prices,
            check_live_trade_exits,
        )
        _ORDER_MONITOR_TICKS += 1
        # Loss limits first: a breach reverts to paper before anything else
        # this tick can submit an order. No-op in paper mode.
        from app.services.risk import check_loss_limits
        limits = check_loss_limits()
        if limits.get("breached"):
            logger.error("Loss limit breached: %s", limits["breached"])
        # Settlement sweep is heavier; run it every 10th tick (~10 min).
        if _ORDER_MONITOR_TICKS % 10 == 1:
            settlement_result = settle_expired_open_trades()
            if settlement_result.get("settled", 0) > 0:
                logger.info("Order monitor: settled %d expired paper trades", settlement_result["settled"])
        result = monitor_live_orders()
        if result.get("filled", 0) > 0:
            logger.info("Order monitor: %d orders filled", result["filled"])
            check_live_prices()
        work = manage_working_orders()
        if work.get("requoted") or work.get("crossed") or work.get("abandoned"):
            logger.info("Order working-loop: %s", work)
        exits = check_live_trade_exits()
        if exits.get("exits_submitted"):
            logger.info("Live risk exits submitted: %s", exits)
        # Reconcile DB vs Kalshi truth every 15th tick (~15 min), live only.
        if _ORDER_MONITOR_TICKS % 15 == 0:
            recon = reconcile_with_kalshi()
            if recon.get("mismatches"):
                logger.error("RECONCILE MISMATCH vs Kalshi: %s", recon["mismatches"])
    except Exception as e:
        logger.error("Order monitor error: %s", e)


def _lifecycle_job():
    try:
        from app.services.trade_lifecycle import check_and_close_trades
        check_and_close_trades()
    except Exception as e:
        logger.error("Lifecycle job error: %s", e)


def _stop_loss_job():
    try:
        from app.services.trade_lifecycle import check_live_prices
        result = check_live_prices()
        if result.get("closed", 0) > 0:
            logger.info("Paper risk exits: closed %d trades", result["closed"])
    except Exception as e:
        logger.error("Paper risk exit job error: %s", e)


def _stale_alert_cleanup():
    """
    Expire pending alerts once they are stale or their market is no longer tradable.
    Extreme prices stay visible as phantom-risk warnings; they are not blocked here.
    """
    try:
        from app import config as cfg
        from app.database import get_conn
        settings = cfg.load()
        expiry_minutes = int(settings.get("stale_alert_expiry_minutes", 60) or 60)
        conn = get_conn()
        result = conn.execute(
            """UPDATE alerts
                  SET status='expired', updated_at=datetime('now')
                WHERE status='pending'
                  AND (
                    datetime(updated_at) <= datetime('now', ?)
                    OR EXISTS (
                      SELECT 1 FROM markets
                       WHERE markets.ticker = alerts.market_ticker
                         AND (
                           lower(coalesce(markets.status, '')) NOT IN ('open', 'active')
                           OR datetime(markets.close_time) <= datetime('now')
                         )
                    )
                  )""",
            (f"-{expiry_minutes} minutes",),
        )
        expired = result.rowcount
        if expired > 0:
            logger.info("Stale alert cleanup: expired %d stale/closed alerts", expired)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("Stale cleanup error: %s", e)


def _stale_data_cleanup_job():
    """Bound SQLite growth and clear stale UI state without touching learning trades."""
    try:
        alert_result = _expire_closed_market_alerts()
        history_result = _cleanup_old_model_history()
        trade_result = _expire_stale_open_trades()
        snapshot_result = _cleanup_old_price_snapshots()
        if (alert_result.get("expired", 0) or history_result.get("deleted", 0)
                or trade_result.get("expired", 0) or snapshot_result.get("deleted", 0)):
            logger.info(
                "Stale data cleanup: alerts=%s history=%s trades=%s snapshots=%s",
                alert_result,
                history_result,
                trade_result,
                snapshot_result,
            )
        return {
            "alerts": alert_result,
            "history": history_result,
            "trades": trade_result,
            "snapshots": snapshot_result,
        }
    except Exception as e:
        logger.error("Stale data cleanup error: %s", e)
        return {"error": str(e)}


def _cleanup_old_price_snapshots(days: int = 14) -> dict:
    """Bound price_snapshots growth. Trade rows are never touched; true CLV and
    close marks are copied onto trades at settlement, so old paths are safe to
    drop after the stop/TP backtest window."""
    from app.database import get_conn

    conn = get_conn()
    try:
        result = conn.execute(
            "DELETE FROM price_snapshots WHERE created_at <= datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        conn.commit()
        return {"deleted": result.rowcount if result.rowcount and result.rowcount > 0 else 0}
    finally:
        conn.close()


def _expire_closed_market_alerts(hours: int = 48) -> dict:
    """Expire old pending/active alerts for closed markets. Rows are not deleted."""
    from app.database import get_conn

    conn = get_conn()
    result = conn.execute(
        """UPDATE alerts
              SET status='expired',
                  details=json_set(coalesce(details, '{}'), '$.expired_reason', 'closed_market_stale'),
                  updated_at=datetime('now')
            WHERE status IN ('pending', 'active', 'paper_traded')
              AND datetime(coalesce(created_at, updated_at)) <= datetime('now', ?)
              AND EXISTS (
                SELECT 1
                  FROM markets
                 WHERE markets.ticker = alerts.market_ticker
                   AND (
                     lower(coalesce(markets.status, '')) NOT IN ('open', 'active')
                     OR (
                       markets.close_time IS NOT NULL
                       AND datetime(markets.close_time) <= datetime('now')
                     )
                   )
              )""",
        (f"-{int(hours)} hours",),
    )
    expired = result.rowcount
    conn.commit()
    conn.close()
    return {"expired": expired, "older_than_hours": int(hours)}


def _cleanup_old_model_history(days: int = 7) -> dict:
    """Delete high-volume model history older than the retention window."""
    from app.database import get_conn

    conn = get_conn()
    model_deleted = conn.execute(
        "DELETE FROM model_outputs WHERE datetime(created_at) < datetime('now', ?)",
        (f"-{int(days)} days",),
    ).rowcount
    snapshots_deleted = conn.execute(
        "DELETE FROM forecast_snapshots WHERE datetime(created_at) < datetime('now', ?)",
        (f"-{int(days)} days",),
    ).rowcount
    conn.commit()
    conn.close()
    return {
        "model_outputs_deleted": model_deleted,
        "forecast_snapshots_deleted": snapshots_deleted,
        "deleted": model_deleted + snapshots_deleted,
        "older_than_days": int(days),
    }


def _expire_stale_open_trades(hours: int = 72) -> dict:
    """Move un-settled open trades on long-closed markets out of the open view."""
    from app.database import get_conn

    conn = get_conn()
    result = conn.execute(
        """UPDATE trades
              SET status='expired',
                  exit_reason='market_expired',
                  exit_price=entry_price,
                  pnl=0.0,
                  clv=NULL,
                  exit_time=datetime('now')
            WHERE status='open'
              AND EXISTS (
                SELECT 1
                  FROM markets
                 WHERE markets.ticker = trades.market_ticker
                   AND markets.close_time IS NOT NULL
                   AND datetime(markets.close_time) <= datetime('now', ?)
                   AND lower(coalesce(markets.status, '')) NOT IN ('open', 'active')
                   AND lower(coalesce(markets.result, '')) NOT IN ('yes', 'no')
              )""",
        (f"-{int(hours)} hours",),
    )
    expired = result.rowcount
    if expired > 0:
        conn.execute(
            """UPDATE orders
                  SET status='expired', updated_at=datetime('now')
                WHERE trade_id IN (
                    SELECT id FROM trades
                     WHERE status='expired'
                       AND exit_reason='market_expired'
                       AND exit_time >= datetime('now', '-1 minute')
                )"""
        )
    conn.commit()
    conn.close()
    return {"expired": expired, "older_than_hours": int(hours)}
