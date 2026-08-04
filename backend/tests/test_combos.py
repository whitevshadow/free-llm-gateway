"""
Combo tests.

Three things are worth testing here, and they are not "does the row round-trip":

  OWNERSHIP     a combo is addressed by an integer id. If ownership were checked
                anywhere but the query, guessing an id would read someone else's
                routing config — so every read must prove it 404s across users.

  RESOLUTION    a combo is a chain that may reference other combos. The cycle
                guard is the difference between a 400 and a worker that spins
                forever, so it is tested directly rather than through the API.

  ORDERING      a strategy's only job is to sequence the targets. Each one must
                return a PERMUTATION — same members, different order — because
                that invariant is what makes fallback total.
"""

import pytest

from app.services import combo_router


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _make(app_client, key: str, **overrides) -> dict:
    payload = {
        "name": "test-combo",
        "strategy": "priority",
        "models": [{"kind": "model", "providerId": "groq", "model": "groq/gpt-oss-120b"}],
    }
    payload.update(overrides)
    response = app_client.post("/v1/me/combos", headers=_headers(key), json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ── CRUD ────────────────────────────────────────────────────────────────────

def test_create_list_and_delete(app_client, user_key):
    combo = _make(app_client, user_key, name="crud-combo")
    assert combo["name"] == "crud-combo"
    assert combo["isActive"] is True
    assert combo["targetCount"] == 1

    listed = app_client.get("/v1/me/combos", headers=_headers(user_key)).json()
    assert any(c["id"] == combo["id"] for c in listed["combos"])

    deleted = app_client.delete(f"/v1/me/combos/{combo['id']}", headers=_headers(user_key))
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True

    gone = app_client.get(f"/v1/me/combos/{combo['id']}", headers=_headers(user_key))
    assert gone.status_code == 404


def test_update_is_partial(app_client, user_key):
    """The dashboard's toggle sends {isActive:false} alone — steps must survive."""
    combo = _make(app_client, user_key, name="partial-combo")

    updated = app_client.put(
        f"/v1/me/combos/{combo['id']}",
        headers=_headers(user_key),
        json={"isActive": False},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["isActive"] is False
    assert body["models"] == combo["models"]      # not wiped by the partial body
    assert body["strategy"] == "priority"


def test_agent_fields_round_trip_at_the_top_level(app_client, user_key):
    """
    The builder sends these four at the top level and reads them back there.
    They are stored inside `config`, so this is the test that the lift back out
    actually happens — without it they would vanish on the next page load.
    """
    combo = _make(
        app_client, user_key, name="agent-combo",
        system_message="You are terse.",
        context_length=32000,
        context_cache_protection=True,
    )
    assert combo["system_message"] == "You are terse."
    assert combo["context_length"] == 32000
    assert combo["context_cache_protection"] is True

    fetched = app_client.get(
        f"/v1/me/combos/{combo['id']}", headers=_headers(user_key)
    ).json()
    assert fetched["system_message"] == "You are terse."

    # An explicit null clears one; the others must survive.
    cleared = app_client.put(
        f"/v1/me/combos/{combo['id']}",
        headers=_headers(user_key),
        json={"system_message": None},
    ).json()
    assert "system_message" not in cleared
    assert cleared["context_length"] == 32000


def test_editing_config_does_not_drop_agent_fields(app_client, user_key):
    """A body carrying BOTH a replacement config and agent knobs keeps both."""
    combo = _make(
        app_client, user_key, name="agent-config-combo", system_message="Stay brief.",
    )
    updated = app_client.put(
        f"/v1/me/combos/{combo['id']}",
        headers=_headers(user_key),
        json={"config": {"maxRetries": 2}},
    ).json()
    assert updated["config"]["maxRetries"] == 2
    assert updated["system_message"] == "Stay brief."


def test_duplicate_name_is_refused(app_client, user_key):
    """A combo name is a model name; two of them would make routing ambiguous."""
    _make(app_client, user_key, name="dupe-combo")
    again = app_client.post(
        "/v1/me/combos",
        headers=_headers(user_key),
        json={"name": "DUPE-COMBO", "models": []},   # case-insensitive clash
    )
    assert again.status_code == 409


def test_unknown_strategy_is_refused(app_client, user_key):
    response = app_client.post(
        "/v1/me/combos",
        headers=_headers(user_key),
        json={"name": "bad-strategy-combo", "strategy": "vibes", "models": []},
    )
    assert response.status_code == 400
    assert "vibes" in response.json()["detail"]


def test_step_without_a_model_is_refused(app_client, user_key):
    """A step the router cannot read would look configured and never fire."""
    response = app_client.post(
        "/v1/me/combos",
        headers=_headers(user_key),
        json={"name": "empty-step-combo", "models": [{"kind": "model", "weight": 50}]},
    )
    assert response.status_code == 400


def test_reorder_ignores_unknown_ids_and_keeps_the_rest(app_client, user_key):
    first = _make(app_client, user_key, name="order-a")
    second = _make(app_client, user_key, name="order-b")

    response = app_client.post(
        "/v1/me/combos/reorder",
        headers=_headers(user_key),
        json={"comboIds": [second["id"], first["id"], "999999"]},
    )
    assert response.status_code == 200
    ordered = [c["id"] for c in response.json()["combos"]]
    assert ordered.index(second["id"]) < ordered.index(first["id"])


# ── ownership ───────────────────────────────────────────────────────────────

def test_another_user_cannot_read_or_delete_my_combo(app_client, user_key, admin_key):
    """
    404, not 403: telling an unauthorised caller that the id EXISTS is itself a
    leak, and an admin has no business reading another user's routing config.
    """
    mine = _make(app_client, user_key, name="private-combo")

    assert app_client.get(
        f"/v1/me/combos/{mine['id']}", headers=_headers(admin_key)
    ).status_code == 404
    assert app_client.delete(
        f"/v1/me/combos/{mine['id']}", headers=_headers(admin_key)
    ).status_code == 404

    listed = app_client.get("/v1/me/combos", headers=_headers(admin_key)).json()
    assert all(c["name"] != "private-combo" for c in listed["combos"])


def test_same_name_is_legal_for_two_users(app_client, user_key, admin_key):
    """Names are unique PER USER — resolution is always scoped by user_id."""
    _make(app_client, user_key, name="shared-name")
    theirs = app_client.post(
        "/v1/me/combos",
        headers=_headers(admin_key),
        json={"name": "shared-name", "models": []},
    )
    assert theirs.status_code == 201


# ── resolution ──────────────────────────────────────────────────────────────

def test_combo_reference_is_expanded(app_client, user_key, db):
    from app.models.combo import Combo

    child = _make(
        app_client, user_key, name="child-combo",
        models=[{"kind": "model", "providerId": "groq", "model": "groq/model-a"}],
    )
    parent = _make(
        app_client, user_key, name="parent-combo",
        models=[
            {"kind": "model", "providerId": "groq", "model": "groq/model-b"},
            {"kind": "combo-ref", "comboName": "child-combo"},
        ],
    )

    row = db.query(Combo).filter(Combo.id == int(parent["id"])).first()
    targets = combo_router.resolve_targets(db, row.user_id, row)

    assert [t.model for t in targets] == ["model-b", "model-a"]
    # The expanded target remembers which combo it came from, which is what makes
    # a test result readable when a chain spans several combos.
    assert targets[1].source_combo == "child-combo"
    assert child["name"] == "child-combo"


def test_cycle_is_refused_not_followed(app_client, user_key, db):
    """`a -> b -> a` must 400. Followed, it is an infinite loop."""
    from app.models.combo import Combo

    a = _make(
        app_client, user_key, name="cycle-a",
        models=[{"kind": "combo-ref", "comboName": "cycle-b"}],
    )
    _make(
        app_client, user_key, name="cycle-b",
        models=[{"kind": "combo-ref", "comboName": "cycle-a"}],
    )

    row = db.query(Combo).filter(Combo.id == int(a["id"])).first()
    with pytest.raises(combo_router.ComboError) as exc:
        combo_router.resolve_targets(db, row.user_id, row)
    assert "references itself" in exc.value.message


def test_dangling_reference_is_skipped_not_fatal(app_client, user_key, db):
    """The rest of a fallback chain must keep serving when one link goes away."""
    from app.models.combo import Combo

    combo = _make(
        app_client, user_key, name="dangling-combo",
        models=[
            {"kind": "combo-ref", "comboName": "no-such-combo"},
            {"kind": "model", "providerId": "groq", "model": "groq/survivor"},
        ],
    )
    row = db.query(Combo).filter(Combo.id == int(combo["id"])).first()
    targets = combo_router.resolve_targets(db, row.user_id, row)
    assert [t.model for t in targets] == ["survivor"]


def test_qualified_model_splits_on_the_first_slash_only(db):
    """Model ids contain slashes of their own ('openai/gpt-oss-120b' at Groq)."""
    step = {"kind": "model", "providerId": "groq", "model": "groq/openai/gpt-oss-120b"}
    target = combo_router._step_targets(step, "any")[0]
    assert target.provider_slug == "groq"
    assert target.model == "openai/gpt-oss-120b"


# ── ordering ────────────────────────────────────────────────────────────────

def _targets(count: int) -> list:
    return [combo_router.Target(model=f"m{i}", provider_slug="p") for i in range(count)]


@pytest.mark.parametrize("strategy", combo_router.STRATEGIES)
def test_every_strategy_returns_a_permutation(strategy, db, user_key, app_client):
    """
    Same members, different order — never a subset.

    This is the invariant fallback rests on: whatever a strategy prefers, an
    exhausted target still hands off to every other target in the combo.
    """
    from app.models.combo import Combo

    created = _make(
        app_client, user_key, name=f"perm-{strategy}", strategy=strategy,
        models=[
            {"kind": "model", "providerId": "p", "model": "p/m0", "weight": 70},
            {"kind": "model", "providerId": "p", "model": "p/m1", "weight": 30},
            {"kind": "model", "providerId": "p", "model": "p/m2"},
        ],
    )
    combo = db.query(Combo).filter(Combo.id == int(created["id"])).first()
    ordered = combo_router.order_targets(db, combo.user_id, combo, _targets(3))
    assert sorted(t.model for t in ordered) == ["m0", "m1", "m2"]


def test_priority_keeps_definition_order(db, user_key, app_client):
    from app.models.combo import Combo

    created = _make(app_client, user_key, name="priority-order", strategy="priority")
    combo = db.query(Combo).filter(Combo.id == int(created["id"])).first()
    ordered = combo_router.order_targets(db, combo.user_id, combo, _targets(3))
    assert [t.model for t in ordered] == ["m0", "m1", "m2"]


def test_round_robin_advances_the_starting_point(db, user_key, app_client):
    from app.models.combo import Combo

    created = _make(app_client, user_key, name="rr-order", strategy="round-robin")
    combo = db.query(Combo).filter(Combo.id == int(created["id"])).first()

    firsts = [
        combo_router.order_targets(db, combo.user_id, combo, _targets(3))[0].model
        for _ in range(3)
    ]
    assert firsts == ["m0", "m1", "m2"]      # rotates, not random


def test_preview_does_not_move_the_round_robin_cursor(db, user_key, app_client):
    """
    Looking at a combo must not change what the next real request does.

    plan(advance=False) is what the Test button and the playground call; if it
    consumed the cursor, opening the preview would silently skip a target.
    """
    from app.models.combo import Combo

    created = _make(app_client, user_key, name="rr-preview", strategy="round-robin")
    combo = db.query(Combo).filter(Combo.id == int(created["id"])).first()

    previews = [
        combo_router.order_targets(
            db, combo.user_id, combo, _targets(3), advance=False,
        )[0].model
        for _ in range(3)
    ]
    assert previews == ["m0", "m0", "m0"]    # same answer every time

    served = combo_router.order_targets(db, combo.user_id, combo, _targets(3))[0].model
    assert served == "m0"                    # the real call still gets target 0


# ── builder options + test preview ──────────────────────────────────────────

def test_builder_options_offers_only_my_providers(app_client, user_key):
    """
    A user with no keys gets an empty provider list — the builder must never
    offer a target the caller cannot reach.
    """
    response = app_client.get("/v1/me/combos/builder-options", headers=_headers(user_key))
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["providers"], list)
    assert isinstance(body["comboRefs"], list)
    for provider in body["providers"]:
        assert provider["connectionCount"] >= 1


def test_test_endpoint_reports_unreachable_steps_instead_of_calling_them(
    app_client, user_key,
):
    """
    Test must not spend quota: it reports whether each step BINDS, and a step
    with no live deployment is 'skipped' with a reason, not an upstream error.
    """
    _make(app_client, user_key, name="preview-combo")
    response = app_client.post(
        "/v1/me/combos/test",
        headers=_headers(user_key),
        json={"comboName": "preview-combo"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["comboName"] == "preview-combo"
    assert body["results"][0]["status"] == "skipped"
    assert "No live deployment" in body["results"][0]["error"]


def test_metrics_omits_combos_with_no_traffic(app_client, user_key):
    """"Never used" and "used, all failed" must not look the same."""
    _make(app_client, user_key, name="untouched-combo")
    body = app_client.get("/v1/me/combos/metrics", headers=_headers(user_key)).json()
    assert "untouched-combo" not in body["metrics"]
