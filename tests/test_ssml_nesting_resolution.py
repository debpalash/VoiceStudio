"""Interleaved nested text must not revisit every open tag per segment."""


def test_property_resolution_work_is_bounded_by_input_tokens(monkeypatch):
    from services import ssml_lite

    class CountingTags(dict):
        lookups = 0

        def __getitem__(self, key):
            self.lookups += 1
            return super().__getitem__(key)

    tags = CountingTags(ssml_lite._TAGS)
    monkeypatch.setattr(ssml_lite, "_TAGS", tags)
    depth = 1000
    got = ssml_lite.parse_ssml_lite("[slow]x" * depth + "[/slow]" * depth)
    assert got == [{"text": "x" * depth, "speed": ssml_lite.SLOW_SPEED,
                    "spell": False, "emphasis": False}]
    assert tags.lookups <= 3 * (2 * depth + 1)
