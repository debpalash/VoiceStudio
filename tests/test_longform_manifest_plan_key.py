"""Recovery grouping ignores job identity and preserves plan differences."""


def test_recovery_plan_key_distinguishes_scripts_and_render_settings():
    from services.longform_resume import manifest_plan_key

    base = {"plan": [{"title": "Book", "spans": [{"text": "Original"}]}], "params": {"format": "m4b"}}
    key = manifest_plan_key(base)
    assert key
    assert manifest_plan_key({**base, "job_id": "another-attempt"}) == key
    assert manifest_plan_key({**base, "params": {"format": "mp3"}}) != key
    assert manifest_plan_key({**base, "plan": [{"title": "Book", "spans": [{"text": "Edited"}]}]}) != key
    assert manifest_plan_key({}) is None
