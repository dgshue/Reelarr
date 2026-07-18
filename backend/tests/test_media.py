"""Frame-selection helpers (pure logic — ffmpeg itself is not exercised)."""

from reelarr.pipeline.media import _evenly_subsample


def test_subsample_returns_all_when_enough():
    assert _evenly_subsample([1, 2, 3], 4) == [1, 2, 3]
    assert _evenly_subsample([1, 2, 3], 3) == [1, 2, 3]


def test_subsample_spreads_and_keeps_endpoints():
    items = list(range(11))
    picked = _evenly_subsample(items, 4)
    assert len(picked) == 4
    assert picked[0] == 0 and picked[-1] == 10
    assert picked == sorted(picked)


def test_subsample_edge_counts():
    assert _evenly_subsample([1, 2, 3], 1) == [1]
    assert _evenly_subsample([1, 2, 3], 0) == [1, 2, 3]
    assert _evenly_subsample([], 4) == []
