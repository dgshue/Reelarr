"""Identification pipeline — the 6-step flow with all externals mocked."""

import pytest

from reelarr.pipeline.identify import IdentificationPipeline, PipelineOutcome
from reelarr.pipeline.media import ClipMetadata
from reelarr.pipeline.tmdb import TmdbMatch

from tests.conftest import FakeResolver, FakeStt, FakeTextLLM, FakeTmdb, FakeVisionLLM

pytestmark = pytest.mark.asyncio

URL = "https://www.tiktok.com/@user/video/123"

HEAT = TmdbMatch(tmdb_id=949, title="Heat", year=1995, media_type="movie")
SEVERANCE = TmdbMatch(tmdb_id=95396, title="Severance", year=2022, media_type="tv")


def make_pipeline(**kwargs) -> IdentificationPipeline:
    defaults = dict(
        resolver=FakeResolver(
            metadata=ClipMetadata(
                platform="tiktok",
                description="best deniro scene #movie",
                hashtags=["#movie"],
                top_comments=["It's Heat (1995)"],
            )
        ),
        text_llm=FakeTextLLM(
            {"title": "Heat", "year": 1995, "type": "movie", "confidence": "high"}
        ),
        stt=FakeStt("You want to be making moves on the street"),
        tmdb=FakeTmdb([HEAT]),
        vision_llm=FakeVisionLLM(),
        enable_vision=False,
    )
    defaults.update(kwargs)
    return IdentificationPipeline(**defaults)


async def test_tier1_high_confidence_single_match_auto_adds():
    pipeline = make_pipeline()
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.AUTO_ADD
    assert result.resolved_tier == "metadata"
    assert result.match is HEAT
    # Tier 2/3 never invoked
    assert pipeline.resolver.calls == ["metadata"]


async def test_tier2_transcript_used_when_tier1_low_confidence():
    text_llm = FakeTextLLM(
        {"title": None, "year": None, "type": None, "confidence": "low"},  # tier 1
        {"title": "Heat", "year": 1995, "type": "movie", "confidence": "high"},  # tier 2
    )
    pipeline = make_pipeline(text_llm=text_llm)
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.AUTO_ADD
    assert result.resolved_tier == "transcript"
    assert "audio" in pipeline.resolver.calls
    # Second LLM call includes the transcript
    assert "AUDIO TRANSCRIPT:" in text_llm.calls[1][1]


async def test_tier3_frames_only_when_vision_enabled():
    text_llm = FakeTextLLM({"title": None, "confidence": "low"})
    vision = FakeVisionLLM({"title": "Heat", "year": 1995, "type": "movie", "confidence": "high"})
    stt = FakeStt("")  # empty transcript -> tier 2 yields nothing

    # Vision disabled -> unidentified, frames never extracted
    p1 = make_pipeline(text_llm=text_llm, stt=stt, vision_llm=vision, enable_vision=False)
    r1 = await p1.run(URL)
    assert r1.outcome == PipelineOutcome.UNIDENTIFIED
    assert "frames" not in p1.resolver.calls

    # Vision enabled -> tier 3 resolves it
    text_llm2 = FakeTextLLM({"title": None, "confidence": "low"})
    p2 = make_pipeline(text_llm=text_llm2, stt=stt, vision_llm=vision, enable_vision=True)
    r2 = await p2.run(URL)
    assert r2.outcome == PipelineOutcome.AUTO_ADD
    assert r2.resolved_tier == "frames"
    assert "frames" in p2.resolver.calls
    assert vision.calls  # vision LLM actually invoked


async def test_tier2_stt_failure_degrades_gracefully():
    text_llm = FakeTextLLM({"title": "Heat", "year": 1995, "type": "movie", "confidence": "medium"})
    resolver = FakeResolver(
        metadata=ClipMetadata(platform="tiktok", description="clip"),
        audio_error=RuntimeError("download failed"),
    )
    pipeline = make_pipeline(text_llm=text_llm, resolver=resolver)
    result = await pipeline.run(URL)
    # Falls back to the tier-1 medium-confidence identification -> confirmation
    assert result.outcome == PipelineOutcome.NEEDS_CONFIRMATION
    assert result.candidates == [HEAT]


async def test_medium_confidence_needs_confirmation():
    pipeline = make_pipeline(
        text_llm=FakeTextLLM({"title": "Heat", "year": 1995, "type": "movie", "confidence": "medium"})
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_CONFIRMATION
    assert [c.title for c in result.candidates] == ["Heat"]


async def test_high_confidence_multiple_exact_matches_needs_confirmation():
    dupe = TmdbMatch(tmdb_id=1000, title="Heat", year=1995, media_type="movie")
    pipeline = make_pipeline(tmdb=FakeTmdb([HEAT, dupe]))
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_CONFIRMATION
    assert len(result.candidates) == 2


async def test_tv_candidates_get_tvdb_ids_resolved():
    tmdb = FakeTmdb([SEVERANCE], tvdb_ids={95396: 371980})
    pipeline = make_pipeline(
        text_llm=FakeTextLLM({"title": "Severance", "year": 2022, "type": "tv", "confidence": "high"}),
        tmdb=tmdb,
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.AUTO_ADD
    assert result.match.tvdb_id == 371980  # Sonarr needs this


async def test_media_type_filter_applied_to_tmdb_matches():
    tv_heat = TmdbMatch(tmdb_id=2, title="Heat", year=2017, media_type="tv")
    pipeline = make_pipeline(tmdb=FakeTmdb([tv_heat, HEAT]))
    result = await pipeline.run(URL)  # LLM said "movie"
    assert result.outcome == PipelineOutcome.AUTO_ADD
    assert result.match is HEAT


async def test_no_tmdb_matches_is_unidentified():
    pipeline = make_pipeline(tmdb=FakeTmdb([]))
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.UNIDENTIFIED
    assert result.identification.title == "Heat"  # LLM guess preserved for the UI


async def test_unknown_all_tiers_is_unidentified():
    pipeline = make_pipeline(text_llm=FakeTextLLM({"title": None, "confidence": "low"}), stt=FakeStt(""))
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.UNIDENTIFIED


async def test_cleanup_always_runs():
    pipeline = make_pipeline()
    await pipeline.run(URL)
    assert pipeline.resolver.cleaned_up == [URL]

    # ...even when the pipeline raises
    failing = make_pipeline(
        resolver=FakeResolver(metadata_error=RuntimeError("yt-dlp exploded"))
    )
    with pytest.raises(RuntimeError):
        await failing.run(URL)
    assert failing.resolver.cleaned_up == [URL]
