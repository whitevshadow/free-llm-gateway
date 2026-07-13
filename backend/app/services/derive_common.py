"""
Derive common models (Phase 5) — group working master models into common models.

Groups working `master_model` rows by `normalized_name`. Any name served by >= 2
DISTINCT providers becomes a `common_model`, and its `common_model_members` are
rewritten in latency order (fastest working deployment first = priority 0).

Idempotent: re-running reconciles — new names appear, names that fell below the
2-provider threshold are deactivated, and member ordering is rebuilt in one pass.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.master_model import MasterModel
from app.models.deployment import Deployment
from app.models.common_model import CommonModel, CommonModelMember

logger = logging.getLogger("gateway.derive")

MIN_PROVIDERS = 2  # "most common" = served by >= 2 providers


def _best_latency(db: Session, master_id: int) -> int:
    """Lowest working-deployment latency for a master model (for ordering)."""
    lat = [
        d.latency_ms or 10_000
        for d in db.query(Deployment).filter(
            Deployment.master_model_id == master_id, Deployment.is_working.is_(True)
        ).all()
    ]
    return min(lat) if lat else 10_000


def derive() -> dict:
    db = SessionLocal()
    try:
        # Group working masters by normalized_name.
        masters = db.query(MasterModel).filter(MasterModel.is_working.is_(True)).all()
        by_name: Dict[str, List[MasterModel]] = {}
        for mm in masters:
            by_name.setdefault(mm.normalized_name, []).append(mm)

        active_names = set()
        created = 0
        for name, group in by_name.items():
            providers = {mm.provider_id for mm in group}
            if len(providers) < MIN_PROVIDERS:
                continue
            active_names.add(name)

            cm = db.query(CommonModel).filter(CommonModel.name == name).first()
            if not cm:
                cm = CommonModel(name=name, display_name=name, auto_generated=True)
                db.add(cm); db.commit(); db.refresh(cm)
                created += 1

            cm.provider_count = len(providers)
            cm.is_active = True
            cm.refreshed_at = datetime.now(timezone.utc)

            # Rewrite members in latency order (clear then re-add to satisfy the
            # UNIQUE(common_model_id, priority) constraint deterministically).
            db.query(CommonModelMember).filter(
                CommonModelMember.common_model_id == cm.id
            ).delete(synchronize_session=False)
            db.commit()

            ordered = sorted(group, key=lambda mm: _best_latency(db, mm.id))
            for priority, mm in enumerate(ordered):
                db.add(CommonModelMember(common_model_id=cm.id, master_model_id=mm.id, priority=priority))
            db.commit()

        # Deactivate common models that no longer meet the threshold.
        deactivated = 0
        for cm in db.query(CommonModel).filter(CommonModel.is_active.is_(True)).all():
            if cm.name not in active_names:
                cm.is_active = False
                deactivated += 1
        db.commit()

        total_active = db.query(CommonModel).filter(CommonModel.is_active.is_(True)).count()
        logger.info("Derive: %d active common model(s) (%d new, %d deactivated).",
                    total_active, created, deactivated)
        return {"active_common_models": total_active, "created": created, "deactivated": deactivated}
    finally:
        db.close()
