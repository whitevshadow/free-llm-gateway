from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List

from app.core.database import get_db
from app.core.config import settings
from app.core.llm_router import router_health
from app.api.auth import get_current_user
from app.models.user import User
from app.models.analytics import TokenUsageLog
from app.schemas.analytics import AnalyticsDashboardResponse, UsageSummaryItem, TokenUsageLogResponse
from app.utils.responses import success_response

router = APIRouter()

@router.get("/dashboard", response_model=AnalyticsDashboardResponse)
def get_dashboard_analytics(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Computes global token usage, cost summaries, and average response times."""
    
    # Query total metrics for this user
    metrics = db.query(
        func.sum(TokenUsageLog.cost).label("total_cost"),
        func.count(TokenUsageLog.id).label("total_requests"),
        func.sum(TokenUsageLog.total_tokens).label("total_tokens"),
        func.avg(TokenUsageLog.latency).label("avg_latency")
    ).filter(TokenUsageLog.user_id == current_user.id).first()

    total_cost = float(metrics.total_cost or 0.0)
    total_requests = int(metrics.total_requests or 0)
    total_tokens = int(metrics.total_tokens or 0)
    average_latency = float(metrics.avg_latency or 0.0)

    # Provider breakdown
    provider_metrics = db.query(
        TokenUsageLog.provider,
        TokenUsageLog.model,
        func.count(TokenUsageLog.id).label("total_requests"),
        func.sum(TokenUsageLog.prompt_tokens).label("total_prompt_tokens"),
        func.sum(TokenUsageLog.completion_tokens).label("total_completion_tokens"),
        func.sum(TokenUsageLog.total_tokens).label("total_tokens"),
        func.sum(TokenUsageLog.cost).label("total_cost"),
        func.avg(TokenUsageLog.latency).label("avg_latency")
    ).filter(TokenUsageLog.user_id == current_user.id).group_by(
        TokenUsageLog.provider, TokenUsageLog.model
    ).all()

    by_provider = []
    for item in provider_metrics:
        by_provider.append(
            UsageSummaryItem(
                provider=item.provider,
                model=item.model,
                total_requests=item.total_requests or 0,
                total_prompt_tokens=item.total_prompt_tokens or 0,
                total_completion_tokens=item.total_completion_tokens or 0,
                total_tokens=item.total_tokens or 0,
                total_cost=float(item.total_cost or 0.0),
                avg_latency=float(item.avg_latency or 0.0)
            )
        )

    # Fetch last 20 logs
    recent_db_logs = db.query(TokenUsageLog).filter(
        TokenUsageLog.user_id == current_user.id
    ).order_by(TokenUsageLog.created_at.desc()).limit(20).all()

    recent_logs = [TokenUsageLogResponse.model_validate(log) for log in recent_db_logs]

    return AnalyticsDashboardResponse(
        total_cost=total_cost,
        total_requests=total_requests,
        total_tokens=total_tokens,
        average_latency=average_latency,
        by_provider=by_provider,
        recent_logs=recent_logs
    )


@router.get("/overview")
def get_overview(days: int = 14, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Dashboard overview — connection status + token usage across ALL gateway traffic.

    Unlike /dashboard (which is scoped to a single UI user), this aggregates EVERY
    TokenUsageLog row, because most traffic comes through the /v1 endpoints (Claude
    Code, OpenAI clients) which are user-less. That's what makes this the right feed
    for a gateway-wide dashboard.
    """
    # ── Totals (all traffic) ────────────────────────────
    totals = db.query(
        func.count(TokenUsageLog.id),
        func.sum(TokenUsageLog.total_tokens),
        func.sum(TokenUsageLog.prompt_tokens),
        func.sum(TokenUsageLog.completion_tokens),
        func.sum(TokenUsageLog.cost),
        func.avg(TokenUsageLog.latency),
    ).first()

    total_requests = int(totals[0] or 0)
    success_count = int(
        db.query(func.count(TokenUsageLog.id))
        .filter(TokenUsageLog.status_code == 200)
        .scalar() or 0
    )
    error_count = total_requests - success_count

    totals_data = {
        "total_requests": total_requests,
        "total_tokens": int(totals[1] or 0),
        "prompt_tokens": int(totals[2] or 0),
        "completion_tokens": int(totals[3] or 0),
        "total_cost": float(totals[4] or 0.0),
        "avg_latency": round(float(totals[5] or 0.0), 3),
        "success_count": success_count,
        "error_count": error_count,
        "success_rate": round(success_count / total_requests, 4) if total_requests else None,
    }

    # ── Per provider/model breakdown ────────────────────
    rows = db.query(
        TokenUsageLog.provider,
        TokenUsageLog.model,
        func.count(TokenUsageLog.id).label("requests"),
        func.sum(TokenUsageLog.total_tokens).label("tokens"),
        func.sum(TokenUsageLog.prompt_tokens).label("prompt_tokens"),
        func.sum(TokenUsageLog.completion_tokens).label("completion_tokens"),
        func.avg(TokenUsageLog.latency).label("avg_latency"),
    ).group_by(TokenUsageLog.provider, TokenUsageLog.model).order_by(func.sum(TokenUsageLog.total_tokens).desc()).all()

    by_model = [
        {
            "provider": r.provider,
            "model": r.model,
            "requests": int(r.requests or 0),
            "tokens": int(r.tokens or 0),
            "prompt_tokens": int(r.prompt_tokens or 0),
            "completion_tokens": int(r.completion_tokens or 0),
            "avg_latency": round(float(r.avg_latency or 0.0), 3),
        }
        for r in rows
    ]

    # ── Daily time series (last `days` days) ────────────
    since = datetime.now(timezone.utc) - timedelta(days=max(days, 1) - 1)
    day_col = func.date(TokenUsageLog.created_at)
    daily_rows = db.query(
        day_col.label("day"),
        func.count(TokenUsageLog.id).label("requests"),
        func.sum(TokenUsageLog.total_tokens).label("tokens"),
    ).filter(TokenUsageLog.created_at >= since).group_by(day_col).all()

    daily_map = {str(r.day): {"requests": int(r.requests or 0), "tokens": int(r.tokens or 0)} for r in daily_rows}
    timeseries = []
    for i in range(max(days, 1)):
        day = (since + timedelta(days=i)).strftime("%Y-%m-%d")
        entry = daily_map.get(day, {"requests": 0, "tokens": 0})
        timeseries.append({"date": day, "requests": entry["requests"], "tokens": entry["tokens"]})

    # ── Recent activity (all traffic) ───────────────────
    recent = (
        db.query(TokenUsageLog)
        .order_by(TokenUsageLog.created_at.desc())
        .limit(25)
        .all()
    )
    recent_logs = [
        {
            "id": log.id,
            "provider": log.provider,
            "model": log.model,
            "total_tokens": log.total_tokens,
            "latency": log.latency,
            "status_code": log.status_code,
            "error_message": log.error_message,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in recent
    ]

    # ── Connection / live pool status ───────────────────
    connection = {
        "configured_providers": settings.get_configured_providers(),
        "router": router_health(),
    }

    return success_response(
        data={
            "totals": totals_data,
            "by_model": by_model,
            "timeseries": timeseries,
            "recent_logs": recent_logs,
            "connection": connection,
        },
        message="Gateway overview",
    )
