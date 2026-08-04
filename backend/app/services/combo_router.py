"""
Combo resolution and execution — turning a combo name into an ordered list of
real deployments, then calling them until one answers.

THREE STAGES, IN ORDER:

  1. RESOLVE   the combo's `models` array -> a flat list of Targets.
               A step is either a model step ("this model, optionally on this
               account") or a reference to another combo, which is expanded
               in place. Cycles are refused, not followed.

  2. ORDER     the Targets by the combo's STRATEGY. This is the only place the
               strategy name means anything: it decides the sequence in which
               targets are tried. Every strategy is a pure reordering, so a
               combo with N targets always tries all N before giving up —
               strategies change the ODDS and the ORDER, never the coverage.

  3. BIND      each Target to the caller's live deployments, and call them in
      + CALL   order until one succeeds. Binding is where "provider + model +
               account" becomes "deployment #412 with this decrypted key".

WHY NOT JUST USE litellm.Router
    The Router load-balances a family name across every deployment behind it.
    That is right for `model: "gpt-oss-120b"` and wrong for a combo, whose whole
    point is that the user said which target comes first and on which account.
    So combos bind targets themselves (llm_router.callable_deployments) and call
    litellm directly. Failures and successes are still written back through
    llm_router.record_failure/record_success, so the DB stays the durable truth
    for health and cooldowns exactly as on the Router path.

WHAT A COMBO CANNOT DO
    It cannot reach a model the caller holds no key for. Targets are bound
    against `v_live_deployments` scoped to the caller, so a combo naming someone
    else's provider simply resolves to zero deployments and is skipped.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import litellm
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import llm_router
from app.core.config import settings
from app.models.combo import Combo
from app.models.request_log import RequestLog
from app.services.normalize import resolve_requested_model

logger = logging.getLogger("gateway.combo")

# How deep combo -> combo references may nest before we refuse. A chain longer
# than this is far likelier to be a mistake than a design.
MAX_DEPTH = 5

# The step-ordering policies. Anything not listed falls back to `priority`
# (definition order), which is why an unknown strategy degrades instead of 500ing.
STRATEGIES = (
    "priority",          # definition order — the combo IS the ordering
    "round-robin",       # rotate the starting point each request
    "strict-random",     # shuffled deck: every target once per cycle
    "random",            # independent shuffle each request
    "weighted",          # weighted draw without replacement (weights from steps)
    "least-used",        # fewest recent requests first
    "cost-optimized",    # free deployments before paid ones
    "fill-first",        # drain one target before moving on (definition order)
    "p2c",               # power-of-two-choices on recent usage
    "auto",              # multi-factor score: recent success rate, then usage
    "lkgp",              # last-known-good first
    "context-relay",     # definition order; handoff behaviour is a config concern
    "reset-aware",       # least recently rate-limited first
    "context-optimized", # widest context window first
)

# Round-robin needs a cursor that survives between requests but not between
# deployments — an in-process counter per (user, combo). Losing it on restart
# costs nothing: the next request simply starts the rotation over.
_rr_cursor: Dict[Tuple[int, str], int] = {}
# strict-random's remaining deck per (user, combo). Refilled when empty.
_deck: Dict[Tuple[int, str], List[str]] = {}


class ComboError(Exception):
    """A combo could not be resolved. Carries an HTTP status for the endpoint."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Target:
    """One resolvable destination inside a combo, before it is bound to a key."""

    # The bare family name the gateway routes on ('gpt-oss-120b').
    model: str
    # Provider slug the step pinned, or None for "any provider serving this model".
    provider_slug: Optional[str] = None
    # A single provider_key id the step pinned ("this account"), or None.
    connection_id: Optional[int] = None
    # An allowlist of provider_key ids to stay within when nothing is pinned.
    allowed_connection_ids: List[int] = field(default_factory=list)
    weight: float = 0.0
    step_id: Optional[str] = None
    label: Optional[str] = None
    # Which combo this target came from — differs from the requested one when a
    # combo-ref was expanded, and is what makes a test result readable.
    source_combo: Optional[str] = None

    @property
    def key(self) -> str:
        """Stable identity, used as the deck/cursor key and in test output."""
        return "|".join([
            self.provider_slug or "*",
            self.model,
            str(self.connection_id or "auto"),
        ])

    def describe(self) -> str:
        base = f"{self.provider_slug or '*'}/{self.model}"
        if self.connection_id:
            return f"{base} · acct {self.connection_id}"
        return base


# ═══════════════════════════════════════════════════════════════════════════
#  LOOKUP
# ═══════════════════════════════════════════════════════════════════════════

def get_combo(db: Session, user_id: int, name: str) -> Optional[Combo]:
    """
    One of MY combos by name. Case-insensitive, because clients spell model
    names however they like and a combo name IS a model name here.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    exact = (
        db.query(Combo)
        .filter(Combo.user_id == user_id, Combo.name == cleaned)
        .first()
    )
    if exact:
        return exact
    return (
        db.query(Combo)
        .filter(Combo.user_id == user_id, func.lower(Combo.name) == cleaned.lower())
        .first()
    )


def callable_combo_names(db: Session, user_id: int) -> List[str]:
    """
    Combo names this user can call RIGHT NOW — they join /v1/models as model ids.

    A combo is listed only when it is active AND at least one of its targets
    binds to a live deployment. That is a stricter test than "the row exists",
    and it is the same rule /v1/models applies to everything else: the list means
    "what you can call", so a name that would answer 503 does not belong on it.

    One deployment fetch for all combos — binding is a set intersection, not a
    query per target.
    """
    combos = (
        db.query(Combo)
        .filter(Combo.user_id == user_id, Combo.is_active.is_(True))
        .order_by(Combo.sort_order, Combo.name)
        .all()
    )
    if not combos:
        return []
    entries = llm_router.callable_deployments(db, user_id)
    if not entries:
        return []

    names: List[str] = []
    for combo in combos:
        try:
            targets = resolve_targets(db, user_id, combo)
        except ComboError:
            # A combo with a cycle is a configuration error, not a callable
            # model. It stays visible and editable in the dashboard; it just
            # cannot be advertised as something a client may request.
            continue
        if any(bind(db, user_id, target, entries) for target in targets):
            names.append(combo.name)
    return names


# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 1 — RESOLVE
# ═══════════════════════════════════════════════════════════════════════════

def _as_int(value: Any) -> Optional[int]:
    """Connection ids arrive from the UI as strings; the DB keys them as ints."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _split_qualified(value: str) -> Tuple[Optional[str], str]:
    """
    '<provider>/<model>' -> (provider, model); a bare name -> (None, name).

    Only the FIRST slash separates, because model ids contain slashes of their
    own ('openai/gpt-oss-120b' at Groq).
    """
    text = (value or "").strip()
    index = text.find("/")
    if index <= 0 or index >= len(text) - 1:
        return None, text
    return text[:index], text[index + 1:]


def _step_targets(step: Any, combo_name: str) -> List[Target]:
    """One raw step (string | model step | combo-ref) -> zero or one Target."""
    if isinstance(step, str):
        provider, model = _split_qualified(step)
        if not model:
            return []
        return [Target(model=model, provider_slug=provider, source_combo=combo_name)]

    if not isinstance(step, dict):
        return []

    raw_model = step.get("model") or ""
    provider, model = _split_qualified(str(raw_model))
    # An explicit providerId wins over whatever the qualified string implied —
    # the builder writes both, and the field is the one it keeps in sync.
    provider = str(step.get("providerId") or step.get("provider") or provider or "") or None
    if not model:
        return []

    allowed = [
        parsed for parsed in
        (_as_int(v) for v in (step.get("allowedConnectionIds") or []))
        if parsed is not None
    ]
    try:
        weight = float(step.get("weight") or 0)
    except (TypeError, ValueError):
        weight = 0.0

    return [Target(
        model=model,
        provider_slug=provider,
        connection_id=_as_int(step.get("connectionId")),
        allowed_connection_ids=allowed,
        weight=weight,
        step_id=step.get("id") or None,
        label=step.get("label") or None,
        source_combo=combo_name,
    )]


def resolve_targets(
    db: Session,
    user_id: int,
    combo: Combo,
    _seen: Optional[Set[str]] = None,
    _depth: int = 0,
) -> List[Target]:
    """
    Flatten a combo's steps into Targets, expanding references to other combos.

    CYCLE GUARD: a combo already on the current expansion path is refused rather
    than followed. Without it, `a -> b -> a` is an infinite loop that takes the
    worker down; with it the user gets a 400 naming the cycle. The guard tracks
    the PATH, not every combo ever seen, so referencing the same helper combo
    from two different branches stays legal.
    """
    seen = set(_seen or ())
    lowered = combo.name.lower()
    if lowered in seen:
        raise ComboError(
            f"Combo {combo.name!r} references itself (directly or through another combo).",
            status=400,
        )
    if _depth > MAX_DEPTH:
        raise ComboError(
            f"Combo {combo.name!r} nests deeper than {MAX_DEPTH} levels.", status=400,
        )
    seen.add(lowered)

    targets: List[Target] = []
    for step in (combo.models or []):
        if isinstance(step, dict) and step.get("kind") == "combo-ref":
            child_name = str(step.get("comboName") or "").strip()
            child = get_combo(db, user_id, child_name) if child_name else None
            if not child:
                # A dangling reference is a configuration error worth SEEING. It
                # is not fatal, though: skipping it leaves the rest of the chain
                # serving, which is the whole point of a fallback list.
                logger.warning(
                    "Combo %r references unknown combo %r — skipped.",
                    combo.name, child_name,
                )
                continue
            targets.extend(resolve_targets(db, user_id, child, seen, _depth + 1))
            continue

        targets.extend(_step_targets(step, combo.name))

    return targets


# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 2 — ORDER
# ═══════════════════════════════════════════════════════════════════════════

def _recent_usage(db: Session, user_id: int, hours: int = 24) -> Dict[str, Dict[str, float]]:
    """
    Per-model request counts and success rate over the last `hours`.

    One grouped query, not one per target: least-used, p2c, auto and lkgp all
    read the same numbers, and a per-target query would put N round trips on the
    hot path of every combo call.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        db.query(
            RequestLog.requested_model,
            func.count(RequestLog.id),
            func.count(RequestLog.id).filter(RequestLog.status_code < 400),
            func.max(RequestLog.created_at),
        )
        .filter(RequestLog.user_id == user_id, RequestLog.created_at >= since)
        .group_by(RequestLog.requested_model)
        .all()
    )
    out: Dict[str, Dict[str, float]] = {}
    for model, total, ok, last in rows:
        out[model] = {
            "requests": float(total or 0),
            "success_rate": (float(ok or 0) / float(total)) if total else 0.0,
            "last_used": last.timestamp() if last else 0.0,
        }
    return out


def _weighted_shuffle(targets: Sequence[Target]) -> List[Target]:
    """
    Draw without replacement, each target's chance proportional to its weight.

    Zero-weight targets are not excluded — they are drawn last, in definition
    order. Dropping them would turn "I only set weights on two of five" into
    silent data loss.
    """
    weighted = [t for t in targets if t.weight > 0]
    unweighted = [t for t in targets if t.weight <= 0]
    ordered: List[Target] = []
    pool = list(weighted)
    while pool:
        total = sum(t.weight for t in pool)
        pick = random.uniform(0, total)
        running = 0.0
        chosen = pool[-1]
        for candidate in pool:
            running += candidate.weight
            if running >= pick:
                chosen = candidate
                break
        ordered.append(chosen)
        pool.remove(chosen)
    return ordered + unweighted


def order_targets(
    db: Session,
    user_id: int,
    combo: Combo,
    targets: List[Target],
    advance: bool = True,
) -> List[Target]:
    """
    Sequence the targets according to the combo's strategy.

    EVERY branch returns a permutation of `targets` — same members, different
    order. That invariant is what makes fallback total: whatever the strategy
    prefers, an exhausted or failing target still hands off to the rest.

    `advance=False` computes the same order WITHOUT consuming rotation state.
    The Test button and the playground both call this, and a preview that moved
    the round-robin cursor would mean looking at a combo changed what the next
    real request does — the observer effect, in a config screen.
    """
    if len(targets) <= 1:
        return list(targets)

    strategy = (combo.strategy or "priority").strip().lower()

    if strategy in ("priority", "fill-first", "context-relay"):
        return list(targets)

    if strategy == "random":
        shuffled = list(targets)
        random.shuffle(shuffled)
        return shuffled

    if strategy == "weighted":
        return _weighted_shuffle(targets)

    if strategy == "round-robin":
        cursor_key = (user_id, combo.name)
        start = _rr_cursor.get(cursor_key, 0) % len(targets)
        if advance:
            _rr_cursor[cursor_key] = (start + 1) % len(targets)
        return targets[start:] + targets[:start]

    if strategy == "strict-random":
        # A shuffled deck: every target is used once before any repeats. The deck
        # holds target KEYS rather than objects so it survives an edit that
        # replaces the step list with equivalent steps.
        deck_key = (user_id, combo.name)
        remaining = [k for k in _deck.get(deck_key, []) if k in {t.key for t in targets}]
        if not remaining:
            remaining = [t.key for t in targets]
            random.shuffle(remaining)
        head = remaining[0]
        if advance:
            _deck[deck_key] = remaining[1:]
        by_key = {t.key: t for t in targets}
        first = by_key.get(head)
        rest = [t for t in targets if t is not first]
        random.shuffle(rest)
        return ([first] if first else []) + rest

    if strategy in ("least-used", "p2c", "auto", "lkgp", "reset-aware"):
        usage = _recent_usage(db, user_id)

        if strategy == "p2c":
            # Power-of-two-choices: sample two candidates, keep the less loaded,
            # then order the rest by load. Cheaper than a global sort under
            # concurrency and avoids the herd effect of always picking the min.
            pair = random.sample(list(targets), 2)
            first = min(pair, key=lambda t: usage.get(t.model, {}).get("requests", 0.0))
            rest = sorted(
                (t for t in targets if t is not first),
                key=lambda t: usage.get(t.model, {}).get("requests", 0.0),
            )
            return [first] + rest

        if strategy == "lkgp":
            # Last known good provider: most recent SUCCESSFUL usage first.
            return sorted(
                targets,
                key=lambda t: -(
                    usage.get(t.model, {}).get("last_used", 0.0)
                    * usage.get(t.model, {}).get("success_rate", 0.0)
                ),
            )

        if strategy == "auto":
            # Success rate dominates; recent load breaks ties. A target with no
            # history scores 0.5 — optimistic enough to be tried, not so
            # optimistic that it outranks something proven.
            def score(t: Target) -> float:
                stats = usage.get(t.model)
                if not stats:
                    return 0.5
                return stats["success_rate"] - min(stats["requests"] / 1000.0, 0.4)

            return sorted(targets, key=score, reverse=True)

        if strategy == "reset-aware":
            # Least recently used first: the longest-idle account is the one
            # most likely to have had its quota window reset.
            return sorted(targets, key=lambda t: usage.get(t.model, {}).get("last_used", 0.0))

        # least-used
        return sorted(targets, key=lambda t: usage.get(t.model, {}).get("requests", 0.0))

    if strategy in ("cost-optimized", "context-optimized"):
        return _order_by_catalog(db, user_id, targets, strategy)

    logger.debug("Unknown combo strategy %r — using definition order.", strategy)
    return list(targets)


def _order_by_catalog(
    db: Session, user_id: int, targets: List[Target], strategy: str,
) -> List[Target]:
    """
    Order by a fact about the MODEL rather than about traffic.

      cost-optimized     free deployments first (provider_models.is_free), then
                         the widest context — the gateway stores no per-token
                         price, so "free before paid" is the honest signal it
                         does have. Ties keep definition order.
      context-optimized  widest context window first.
    """
    from app.models.provider_model import ProviderModel

    names = {t.model for t in targets}
    rows = (
        db.query(
            ProviderModel.normalized_name,
            func.bool_or(ProviderModel.is_free),
            func.max(ProviderModel.context_window),
        )
        .filter(ProviderModel.normalized_name.in_(names))
        .group_by(ProviderModel.normalized_name)
        .all()
    )
    facts = {name: (bool(free), int(ctx or 0)) for name, free, ctx in rows}

    if strategy == "context-optimized":
        return sorted(targets, key=lambda t: -facts.get(t.model, (False, 0))[1])

    # cost-optimized: free first, then widest context.
    return sorted(
        targets,
        key=lambda t: (
            0 if facts.get(t.model, (False, 0))[0] else 1,
            -facts.get(t.model, (False, 0))[1],
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 3 — BIND
# ═══════════════════════════════════════════════════════════════════════════

def bind(
    db: Session, user_id: int, target: Target, entries: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Which of the caller's live deployments satisfy this target, best first.

    Matching narrows in three steps — model, then provider, then account — and
    each is a FILTER, never a fallback: a target that pinned an account and whose
    account is currently cooling down resolves to nothing and the combo moves on.
    Quietly serving it from a different account would defeat the reason someone
    pinned one.
    """
    available = {e["model"] for e in entries}
    # The client's spelling of a model may not be the catalog's; the same
    # normalisation the direct path uses applies here so a combo written as
    # 'openai/gpt-oss-120b' binds to 'gpt-oss-120b'.
    resolved = resolve_requested_model(target.model, available)
    if not resolved:
        return []

    matches = [e for e in entries if e["model"] == resolved]

    if target.provider_slug:
        by_provider = [e for e in matches if e["provider_slug"] == target.provider_slug]
        # A provider that no longer serves this model is a stale step, not a
        # reason to call someone else's provider — but neither is it a reason to
        # drop a target the user can still serve elsewhere IF they never pinned
        # an account. Keep the pin strict only when it actually matched something.
        if by_provider:
            matches = by_provider
        else:
            logger.debug(
                "Combo target %s: provider %r serves no live deployment of %r.",
                target.describe(), target.provider_slug, resolved,
            )
            return []

    if target.connection_id:
        matches = [e for e in matches if e["provider_key_id"] == target.connection_id]
    elif target.allowed_connection_ids:
        allowed = set(target.allowed_connection_ids)
        matches = [e for e in matches if e["provider_key_id"] in allowed]

    return matches


@dataclass
class Attempt:
    """What happened on one target, for the test/simulate views."""

    target: Target
    status: str                     # 'ok' | 'error' | 'skipped'
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    deployment_id: Optional[int] = None


def plan(
    db: Session, user_id: int, combo: Combo, advance: bool = False,
) -> List[Tuple[Target, List[Dict[str, Any]]]]:
    """
    The full ordered plan: every target with the deployments it binds to.

    Shared by execution, the combo test and the playground simulation, so all
    three describe the SAME routing — a preview that used different logic from
    the real call would be worse than no preview.

    `advance` defaults to FALSE so the read-only callers cannot rotate anything
    by accident; execution opts in explicitly.
    """
    targets = order_targets(
        db, user_id, combo, resolve_targets(db, user_id, combo), advance=advance,
    )
    entries = llm_router.callable_deployments(db, user_id)
    return [(t, bind(db, user_id, t, entries)) for t in targets]


# ═══════════════════════════════════════════════════════════════════════════
#  EXECUTE
# ═══════════════════════════════════════════════════════════════════════════

def _retry_config(combo: Combo) -> Tuple[int, float]:
    """(max set retries, delay in seconds) from the combo's runtime config."""
    config = combo.config if isinstance(combo.config, dict) else {}
    try:
        retries = int(config.get("maxSetRetries") or 0)
    except (TypeError, ValueError):
        retries = 0
    try:
        delay_ms = float(config.get("setRetryDelayMs") or 0)
    except (TypeError, ValueError):
        delay_ms = 0.0
    return max(0, min(retries, 3)), max(0.0, min(delay_ms, 10_000.0)) / 1000.0


async def acompletion(
    db: Session,
    user_id: int,
    combo: Combo,
    body: Dict[str, Any],
    stream: bool = False,
) -> Tuple[Any, Attempt, int]:
    """
    Call the combo's targets in order until one answers.

    Returns (response, winning attempt, 1-based attempt number). Raises ComboError
    when every target failed — with the LAST upstream error attached, because a
    generic "all targets failed" hides the one thing worth reading.

    Health bookkeeping goes through llm_router.record_failure/record_success, so
    a 429 seen here cools that deployment down in Postgres for every path — the
    combo path and the plain Router path share one source of truth.
    """
    if combo.is_active is False:
        raise ComboError(f"Combo {combo.name!r} is disabled.", status=409)

    steps = plan(db, user_id, combo, advance=True)
    if not steps:
        raise ComboError(
            f"Combo {combo.name!r} has no steps. Add at least one model.", status=400,
        )

    callable_steps = [(t, deps) for t, deps in steps if deps]
    if not callable_steps:
        raise ComboError(
            f"Combo {combo.name!r} has no callable target right now — every step "
            f"resolves to a model, provider or account you cannot currently reach.",
            status=503,
        )

    set_retries, retry_delay = _retry_config(combo)
    # `model` and `stream` are supplied per target (the bound deployment's own
    # model id, and the caller's streaming choice), so they must not also arrive
    # from the body — litellm would see them twice.
    call_body = {k: v for k, v in body.items() if k not in ("model", "stream")}
    litellm.request_timeout = settings.REQUEST_TIMEOUT

    attempt_number = 0
    last_error: Optional[Exception] = None

    for round_index in range(set_retries + 1):
        if round_index:
            logger.info(
                "Combo %r: retrying the whole target set (%d/%d).",
                combo.name, round_index, set_retries,
            )
            if retry_delay:
                import asyncio
                await asyncio.sleep(retry_delay)

        for target, deployments in callable_steps:
            for entry in deployments:
                attempt_number += 1
                started = time.perf_counter()
                try:
                    response = await litellm.acompletion(
                        **entry["params"], **call_body, stream=stream,
                    )
                except Exception as exc:
                    latency = int((time.perf_counter() - started) * 1000)
                    last_error = exc
                    logger.info(
                        "Combo %r target %s failed (%dms): %s",
                        combo.name, target.describe(), latency, exc,
                    )
                    # Durable truth: cool this deployment down / trip the circuit,
                    # exactly as a failure on the Router path would.
                    llm_router.record_failure(entry["deployment_id"], exc)
                    continue

                llm_router.record_success(entry["deployment_id"])
                return (
                    response,
                    Attempt(
                        target=target,
                        status="ok",
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        deployment_id=entry["deployment_id"],
                    ),
                    attempt_number,
                )

    detail = f"Combo {combo.name!r}: all {attempt_number} target attempt(s) failed."
    if last_error:
        detail = f"{detail} Last error: {last_error}"
    raise ComboError(detail, status=502)


# ═══════════════════════════════════════════════════════════════════════════
#  TEST + SIMULATE — the same plan, described instead of called
# ═══════════════════════════════════════════════════════════════════════════

def dry_run(db: Session, user_id: int, combo: Combo) -> Dict[str, Any]:
    """
    Resolve the combo and report what WOULD be called, without spending a token.

    This is what the dashboard's "Test" button shows. It reports resolution, not
    liveness: a target listed `ok` here has a live deployment bound to it right
    now, which is the question a config screen can actually answer.
    """
    steps = plan(db, user_id, combo)
    results = []
    resolved_by = None
    resolved_target = None

    for target, deployments in steps:
        entry = {
            "model": target.describe(),
            "label": target.label or target.describe(),
            "stepId": target.step_id,
            "connectionId": str(target.connection_id) if target.connection_id else None,
            "status": "ok" if deployments else "skipped",
            "deployments": len(deployments),
        }
        if not deployments:
            entry["error"] = (
                "No live deployment matches this step — check the model, the "
                "provider and the pinned account."
            )
        elif resolved_by is None:
            resolved_by = target.describe()
            resolved_target = {
                "stepId": target.step_id,
                "connectionId": str(target.connection_id) if target.connection_id else None,
            }
        results.append(entry)

    payload: Dict[str, Any] = {
        "comboName": combo.name,
        "strategy": combo.strategy,
        "results": results,
    }
    if resolved_by:
        payload["resolvedBy"] = resolved_by
        payload["resolvedByTarget"] = resolved_target
    else:
        payload["error"] = (
            f"Combo {combo.name!r} has no callable target right now."
            if results
            else f"Combo {combo.name!r} has no steps yet."
        )
    return payload
