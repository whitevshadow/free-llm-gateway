"""
Recompute `provider_models.normalized_name` from `litellm_model`.

WHY THIS EXISTS. The normalized name is the family key that groups the same
model across providers (services/normalize.py) — it is what makes two providers
serving one model a fallback pair instead of two unrelated rows. It is computed
at DISCOVERY time and then stored, so when the normalization RULES change,
existing rows keep the key they were written with. Nothing drifts back into line
on its own.

Discovery does rewrite the field, but only for providers you hold a live key
for, and only by spending a /models call on each. This is the offline path: pure
recomputation from a column already in the table, no provider contacted, no
quota spent.

WHAT IT DOES NOT DO: touch `provider_count` or `is_common`. Those are maintained
by trg_pmodels_common, which fires per STATEMENT on the update below — writing
them here would fight the trigger for the same field (see catalog.py).

Renaming a family key changes the PUBLIC model id (SRS §12, §16). That is the
point when the old key was wrong, but it is a rename, so this defaults to a dry
run and prints every change before anything is written.

Usage:
    python backend/tools/renormalize_models.py            # dry run — show the diff
    python backend/tools/renormalize_models.py --apply    # write it
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.models.provider import Provider  # noqa: E402
from app.models.provider_model import ProviderModel  # noqa: E402
from app.services import nvcf, nvidia_riva  # noqa: E402
from app.services.normalize import normalize_model_name  # noqa: E402


def _is_recomputable(litellm_model: str) -> bool:
    """
    Whether `normalize_model_name` is the right function for this row.

    It assumes `<litellm provider prefix>/<upstream id>` and discards the first
    segment. Three kinds of row do not have that shape, and feeding them through
    it produces garbage rather than a no-op:

      nvcf/<uuid>            the id IS a cloud-function uuid; stripping the
                             prefix leaves the uuid as the public model name.
      nvidia_riva/…#<uuid>   carries its function id as a '#' suffix, which is
                             not part of the family key.
      <bare id>              never prefixed at all, so the first segment is a
                             real part of the name.

    Discovery writes all three through the correct path already (catalog.py),
    so they are skipped rather than "fixed".
    """
    if not litellm_model or "/" not in litellm_model:
        return False
    if litellm_model.startswith(nvcf.MODEL_PREFIX):
        return False
    if litellm_model.startswith(nvidia_riva.MODEL_PREFIX):
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = (
            db.query(ProviderModel, Provider)
            .join(Provider, Provider.id == ProviderModel.provider_id)
            .order_by(Provider.slug, ProviderModel.normalized_name)
            .all()
        )

        changes = []
        skipped = 0
        for pm, prov in rows:
            if not _is_recomputable(pm.litellm_model or ""):
                skipped += 1
                continue
            new = normalize_model_name(pm.litellm_model)
            if new != pm.normalized_name:
                changes.append((prov.slug, pm, pm.normalized_name, new))

        if not changes:
            print(
                f"{len(rows)} models ({skipped} on special surfaces, skipped) — "
                "every normalized_name already current."
            )
            return 0

        print(
            f"{len(changes)} of {len(rows)} models would be renamed "
            f"({skipped} on special surfaces, skipped):\n"
        )
        for slug, _pm, old, new in changes:
            print(f"  {slug:<14} {old:<48} -> {new}")

        if not args.apply:
            print("\nDry run. Re-run with --apply to write.")
            return 0

        for _slug, pm, _old, new in changes:
            pm.normalized_name = new
        db.commit()
        print(f"\nApplied {len(changes)} rename(s). is_common was recomputed by the trigger.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
