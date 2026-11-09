"""#3062: a chunk boundary must never cut a token in two.

Both splitters treated punctuation at the edge of what they were looking at as
a boundary: ``split_text_into_chunks`` judged a ``max_chars`` window on its own,
so a window ending inside ``3,5`` / ``e.g.`` / ``site.com`` / ``...`` looked like
a clause or sentence end; the model's internal ``chunk_text_punctuation`` split
at every ASCII ``.,;:!?`` even mid-number. Each half was then rendered as its
own utterance, so the join spoke different words than the script.
"""

import random

import pytest



# Resolved at call time: another test may replace these modules in sys.modules.
def chunk_text_punctuation(*args, **kwargs):
    from omnivoice.utils.text import chunk_text_punctuation as impl

    return impl(*args, **kwargs)


def split_text_into_chunks(*args, **kwargs):
    from services.chunked_tts import split_text_into_chunks as impl

    return impl(*args, **kwargs)


def _words(chunks):
    return " ".join(chunks).split()


@pytest.mark.parametrize("token", ["3,5", "3:5", "e.g.x", "site.com", "a...b", "x;y", "ok!?"])
def test_window_edge_inside_a_token_is_not_a_boundary(token):
    # Place the token so the 40-char window ends right after its punctuation.
    cut = token.index(next(ch for ch in token if ch in ".,;:!?"))
    filler = "x" * (40 - 1 - cut - 1)
    text = f"{filler} {token} and more words follow here."
    assert len(f"{filler} {token[:cut + 1]}") == 40
    chunks = split_text_into_chunks(text, 40)
    assert _words(chunks) == text.split()
    assert all(len(c) <= 40 for c in chunks)


def test_real_boundary_at_window_edge_still_splits_there():
    text = "x" * 38 + ", tail words that keep going for a while."
    chunks = split_text_into_chunks(text, 40)
    assert chunks[0] == "x" * 38 + ","


def test_splitter_preserves_every_word_fuzz():
    rng = random.Random(3062)
    vocab = ("a fixa documentario, Brasil. 3,5 3.5 site.com Dr. e.g. "
             "[pause 300ms] — ... ! ? x;y").split()
    for _ in range(400):
        text = " ".join(rng.choice(vocab) for _ in range(rng.randint(40, 300)))
        for max_chars in (60, 120, 800):
            chunks = split_text_into_chunks(text, max_chars)
            assert _words(chunks) == text.split()


def test_stitched_render_speaks_each_word_once():
    """Mock engine: each chunk 'renders' as its own words; the join must
    reproduce the script exactly — no duplicated or split word at a seam."""
    text = ("O custo foi de 3,5 milhoes, segundo o site.com oficial. " * 30).strip()
    spoken = []
    for chunk in split_text_into_chunks(text, 100):
        spoken.extend(chunk.split())
    assert spoken == text.split()


@pytest.mark.parametrize("token", ["3,5", "3.5", "site.com", "e.g", "x:y"])
def test_model_chunker_does_not_split_inside_a_token(token):
    text = f"Alpha beta {token} gamma delta. Epsilon zeta eta theta."
    for chunk_len in range(4, len(text)):
        chunks = chunk_text_punctuation(text, chunk_len=chunk_len, min_chunk_len=3)
        assert token in " ".join(chunks).split(), (chunk_len, chunks)


def test_model_chunker_still_splits_at_real_punctuation():
    chunks = chunk_text_punctuation("One two, three four. Five six!", chunk_len=10, min_chunk_len=3)
    assert chunks == ["One two,", "three four.", "Five six!"]


def test_no_space_cjk_sentences_after_ascii_marks_still_split():
    text = "你好吗?我很好!谢谢你。" * 20
    chunks = chunk_text_punctuation(text, chunk_len=20)
    assert len(chunks) > 1
    assert "".join(chunks).replace(" ", "") == text


def test_a_punctuation_run_inside_a_token_stays_whole():
    chunks = chunk_text_punctuation("wait...what is this. " * 10, chunk_len=12)
    assert all("wait...what" in c or "wait" not in c for c in chunks)
