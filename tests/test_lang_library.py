"""beast-lang library acquirer — the selection rule that must not regress.

The C draft resolver is the one piece of this with a non-obvious failure mode,
and it already bit us: WG14's index lists every PAPER, carries no titles, and
"highest N-numbered PDF" selected n3962 — a two-page note called "clarify
H.11.4 encoding conversion requirements". We would have shipped a two-page
paper labelled "the C standard". A corpus that is confidently wrong is worse
than an empty one, because an agent will cite it.

These tests are offline: head_size is replaced, so nothing here touches the
network.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))

import wg14_pick  # noqa: E402

INDEX = """
<html><body>
<a href="n3880.pdf">n3880</a>
<a href="n3886.pdf">n3886</a>
<a href="n3960.pdf">n3960</a>
<a href="n3962.pdf">n3962</a>
</body></html>
"""

# n3886 is the draft (793 pages); everything newer here is a short paper.
SIZES = {
    "n3880.pdf": 40_000,
    "n3886.pdf": 3_400_000,
    "n3960.pdf": 90_000,
    "n3962.pdf": 120_000,
}


@pytest.fixture()
def index(tmp_path):
    p = tmp_path / "wg14-index.html"
    p.write_text(INDEX)
    return str(p)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(wg14_pick, "head_size",
                        lambda url, timeout=20.0: SIZES.get(os.path.basename(url), 0))


def test_candidates_are_newest_first(index):
    assert wg14_pick.candidates(open(index).read(), 40) == [3962, 3960, 3886, 3880]


def test_the_draft_wins_over_newer_papers(index):
    url, size = wg14_pick.pick(open(index).read(), 40, 1_000_000)
    assert url.endswith("n3886.pdf"), "picked a paper over the draft"
    assert size == 3_400_000


def test_a_paper_only_index_is_refused_not_accepted(index, monkeypatch):
    """The important half. If no draft is present the answer is NOTHING, so
    the caller fails loudly, rather than the largest paper on offer."""
    monkeypatch.setattr(wg14_pick, "head_size",
                        lambda url, timeout=20.0: 120_000)
    assert wg14_pick.pick(open(index).read(), 40, 1_000_000) is None


def test_one_unreachable_candidate_does_not_abort_the_sweep(index, monkeypatch):
    """pick() walks every candidate, so a single dead URL must not lose the
    draft that comes after it. head_size absorbs errors by contract (returns
    0), and this proves pick() still finds n3886 when a newer one fails."""
    def flaky(url, timeout=20.0):
        if url.endswith("n3962.pdf"):
            return 0                     # what head_size does on a failure
        return SIZES.get(os.path.basename(url), 0)
    monkeypatch.setattr(wg14_pick, "head_size", flaky)
    url, size = wg14_pick.pick(open(index).read(), 40, 1_000_000)
    assert url.endswith("n3886.pdf") and size == 3_400_000


def test_head_size_returns_zero_instead_of_raising(monkeypatch):
    """The contract the test above depends on: one unreachable URL is a 0,
    not an exception that ends the sweep."""
    import urllib.request

    def explode(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(urllib.request, "urlopen", explode)
    assert wg14_pick.head_size("https://example.invalid/n1.pdf") == 0
