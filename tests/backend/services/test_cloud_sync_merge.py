"""Three-way merge for synced settings (desktop-app-guide §7)."""
import string

import pytest

from services.cloud_sync.ids import new_sync_id
from services.cloud_sync.merge import canonical, merge3, merge_item


def test_sync_ids_are_16_base62_and_unique():
    ids = {new_sync_id() for _ in range(2000)}
    assert len(ids) == 2000
    allowed = set(string.ascii_letters + string.digits)
    assert all(len(i) == 16 and set(i) <= allowed for i in ids)


def test_canonical_json_sorts_keys_and_drops_whitespace():
    assert canonical({"b": 1, "a": {"d": [1, 2], "c": None}}) == '{"a":{"c":null,"d":[1,2]},"b":1}'


# The guide's six cases, verbatim.
@pytest.mark.parametrize(
    "base,local,remote,expected,conflicts",
    [
        ({"theme": "dark"}, {"theme": "dark"}, {"theme": "light"}, {"theme": "light"}, []),
        (
            {"theme": "dark", "locale": "en"},
            {"theme": "light", "locale": "en"},
            {"theme": "dark", "locale": "de"},
            {"theme": "light", "locale": "de"},
            [],
        ),
        ({"theme": "dark"}, {"theme": "light"}, {"theme": "system"}, {"theme": "light"}, ["theme"]),
        (
            {"a": {"added_at": "t1"}},
            {"a": {"added_at": "t1"}, "b": {"added_at": "t2"}},
            {},
            {"b": {"added_at": "t2"}},
            [],
        ),
        (None, {"theme": "dark"}, {"theme": "light", "locale": "en"}, {"theme": "dark", "locale": "en"}, ["theme"]),
        ({"steps": 16}, {"steps": 16, "x_future": 1}, {"steps": 24, "x_future": 1}, {"steps": 24, "x_future": 1}, []),
    ],
)
def test_guide_cases(base, local, remote, expected, conflicts):
    result = merge3(base, local, remote)
    assert result.values == expected
    assert result.conflicts == conflicts


X, Y, Z = {"v": 1}, {"v": 2}, {"v": 3}
A = object()  # "absent"


@pytest.mark.parametrize(
    "b,l,r,expected,conflict",
    [
        (X, X, Y, Y, False),
        (X, Y, X, Y, False),
        (X, Y, Y, Y, False),
        (X, Y, Z, Y, True),
        (A, Y, A, Y, False),
        (A, A, Z, Z, False),
        (A, Y, Z, Y, True),
        (A, Y, Y, Y, False),
        (X, A, X, A, False),
        (X, X, A, A, False),
        (X, A, Z, Z, False),
        (X, Y, A, Y, False),
        (X, A, A, A, False),
    ],
)
def test_every_row_of_the_merge_table(b, l, r, expected, conflict):
    pack = lambda v: {} if v is A else {"k": v}  # noqa: E731
    result = merge3(pack(b), pack(l), pack(r))
    assert result.values == pack(expected)
    assert result.conflicts == (["k"] if conflict else [])


def test_values_compare_as_canonical_json():
    result = merge3({"k": {"a": 1, "b": 2}}, {"k": {"b": 2, "a": 1}}, {"k": {"a": 1, "b": 3}})
    assert result.values == {"k": {"a": 1, "b": 3}}
    assert result.conflicts == []


def test_item_level_an_edit_beats_a_delete():
    base = {"theme": "dark"}
    assert merge_item(base, None, {"theme": "light"}).values == {"theme": "light"}
    assert merge_item(base, {"theme": "light"}, None).values == {"theme": "light"}
    assert merge_item(base, None, base).values is None
    assert merge_item(base, base, None).values is None
    assert merge_item(base, None, None).values is None
    assert merge_item(None, None, {"a": 1}).values == {"a": 1}
