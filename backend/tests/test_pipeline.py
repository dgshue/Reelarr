"""Identification pipeline — the 6-step flow with all externals mocked."""

import pytest

from reelarr.pipeline.identify import IdentificationPipeline, PipelineOutcome
from reelarr.pipeline.media import ClipMetadata
from reelarr.pipeline.tmdb import PersonCredit, TmdbMatch

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


def make_tier3_pipeline(**kwargs):
    """Pipeline where tiers 1-2 fail and tier 3 evidence points at Heat (1995).

    Vision OCRs subtitles naming Neil + Vincent and recognizes De Niro +
    Pacino; TMDB filmography intersection surfaces Heat; credit verification
    finds both character names -> verified high.
    """
    defaults = dict(
        resolver=FakeResolver(
            metadata=ClipMetadata(platform="tiktok", description="#movie #fyp"),
            frames=["ZmFrZS1qcGVn", "ZnJhbWUy"],
        ),
        text_llm=FakeTextLLM(
            {"title": None, "confidence": "low"},  # tier 1
            {   # tier 3 evidence call
                "candidates": [
                    {"title": "Heat", "year": 1995, "type": "movie", "confidence": "medium"}
                ],
                "character_names": ["Neil", "Vincent"],
            },
        ),
        stt=FakeStt(""),  # empty transcript -> tier 2 yields nothing
        tmdb=FakeTmdb(
            matches=[HEAT],
            person_ids={"Robert De Niro": 380, "Al Pacino": 1158},
            person_credits={
                380: [PersonCredit("movie", 949, "Heat", 1995, 9.0, "Neil McCauley")],
                1158: [PersonCredit("movie", 949, "Heat", 1995, 10.0, "Vincent Hanna")],
            },
            cast_characters={("movie", 949): ["Neil McCauley", "Vincent Hanna"]},
        ),
        vision_llm=FakeVisionLLM(
            describe_response=(
                "ON-SCREEN TEXT: 'Neil, what do you say?' / 'Vincent, drop the gun.' "
                "— English subtitles."
            ),
            actor_response={
                "actors": [
                    {"name": "Robert De Niro", "confidence": "likely"},
                    {"name": "Al Pacino", "confidence": "likely"},
                ]
            },
        ),
        enable_vision=True,
    )
    defaults.update(kwargs)
    return IdentificationPipeline(**defaults)


async def test_tier3_frames_only_when_vision_enabled():
    # Vision disabled -> unidentified, frames never extracted
    p1 = make_tier3_pipeline(enable_vision=False)
    r1 = await p1.run(URL)
    assert r1.outcome == PipelineOutcome.UNIDENTIFIED
    assert "frames" not in p1.resolver.calls

    # Vision enabled -> tier 3 resolves it
    p2 = make_tier3_pipeline()
    r2 = await p2.run(URL)
    assert r2.outcome == PipelineOutcome.AUTO_ADD
    assert r2.resolved_tier == "frames"
    assert "frames" in p2.resolver.calls
    assert p2.vision_llm.calls  # vision LLM actually invoked


async def test_tier3_verified_match_pins_exact_tmdb_entry():
    pipeline = make_tier3_pipeline()
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.AUTO_ADD
    assert result.identification.confidence == "high"
    assert result.match.tmdb_id == 949  # from credit verification, not re-search
    # Both kinds of vision calls happened: describe chunk(s) + per-frame actor calls
    systems = [c[0] for c in pipeline.vision_llm.calls]
    assert any("describe" in s.lower() for s in systems)
    assert any("actors" in s.lower() for s in systems)
    # Actor recognition is one frame per call (batching degrades accuracy)
    actor_calls = [c for c in pipeline.vision_llm.calls if "actors" in c[0].lower()]
    assert all(len(c[2]) == 1 for c in actor_calls)


async def test_tier3_contradicted_candidate_demoted_to_low():
    """Character names present but no candidate's credits contain them ->
    the LLM's title guess must NOT auto-add, however confident it claims to be."""
    pipeline = make_tier3_pipeline(
        text_llm=FakeTextLLM(
            {"title": None, "confidence": "low"},
            {   # LLM claims high confidence on a wrong title
                "candidates": [
                    {"title": "Heat", "year": 1995, "type": "movie", "confidence": "high"}
                ],
                "character_names": ["Tripp", "Jeffrey"],
            },
        ),
        tmdb=FakeTmdb(
            matches=[HEAT],
            person_ids={"Robert De Niro": 380, "Al Pacino": 1158},
            person_credits={
                380: [PersonCredit("movie", 949, "Heat", 1995, 9.0, "Neil McCauley")],
                1158: [PersonCredit("movie", 949, "Heat", 1995, 10.0, "Vincent Hanna")],
            },
            cast_characters={("movie", 949): ["Neil McCauley", "Vincent Hanna"]},
        ),
        vision_llm=FakeVisionLLM(
            describe_response=(
                "ON-SCREEN TEXT: 'Amy is Tripp's first love' / "
                "'Jeffrey is not Tripp's nephew' — English subtitles."
            ),
            actor_response={
                "actors": [
                    {"name": "Robert De Niro", "confidence": "likely"},
                    {"name": "Al Pacino", "confidence": "likely"},
                ]
            },
        ),
    )
    result = await pipeline.run(URL)
    assert result.outcome != PipelineOutcome.AUTO_ADD
    assert result.identification.confidence == "low"


async def test_tier3_unverifiable_candidate_capped_at_medium():
    """No character names extracted -> nothing to verify against -> a bare
    LLM guess is capped at medium so it goes to confirmation, never auto-add."""
    pipeline = make_tier3_pipeline(
        text_llm=FakeTextLLM(
            {"title": None, "confidence": "low"},
            {
                "candidates": [
                    {"title": "Heat", "year": 1995, "type": "movie", "confidence": "high"}
                ],
                "character_names": [],
            },
        ),
        vision_llm=FakeVisionLLM(
            describe_response="Two men talking in a diner. No on-screen text.",
            actor_response={"actors": []},
        ),
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_CONFIRMATION
    assert result.identification.confidence == "medium"
    assert result.resolved_tier == "frames"


async def test_tier3_ungrounded_character_names_cannot_self_verify():
    """The LLM proposes 'Heat' AND claims character names that do not occur
    anywhere in the evidence (hallucinated to fit its own guess). The
    grounding guard must discard them, leaving nothing to verify -> capped at
    medium, never a verified high."""
    pipeline = make_tier3_pipeline(
        text_llm=FakeTextLLM(
            {"title": None, "confidence": "low"},
            {
                "candidates": [
                    {"title": "Heat", "year": 1995, "type": "movie", "confidence": "high"}
                ],
                # Correct characters for Heat — but absent from the evidence.
                "character_names": ["Neil", "Vincent"],
            },
        ),
        vision_llm=FakeVisionLLM(
            describe_response="Two men in a diner. No on-screen text.",
            actor_response={"actors": []},
        ),
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_CONFIRMATION
    assert result.identification.confidence == "medium"
    # Verification never ran: no credit lookups were made
    assert pipeline.tmdb.credit_lookups == []


def test_character_hits_prefix_tolerance():
    hits = IdentificationPipeline._character_hits
    # STT drift: "Trip" (heard) matches credited "Tripp"
    assert hits(["Trip", "Ace"], ["Tripp", "Paula", "Ace Goldberg", "Kit"]) == 2
    # Nickname prefix: "Jeff" matches "Jeffrey"
    assert hits(["Jeff"], ["Jeffrey"]) == 1
    # Short names must match exactly (no "Al" ~ "Alan")
    assert hits(["Al"], ["Alan"]) == 0
    assert hits(["Al"], ["Al"]) == 1
    # Multi-word names require every word
    assert hits(["Neil McCauley"], ["Neil McCauley"]) == 1
    assert hits(["Neil Diamond"], ["Neil McCauley"]) == 0


async def test_tier3_garbage_evidence_stays_unidentified():
    pipeline = make_tier3_pipeline(
        text_llm=FakeTextLLM(
            {"title": None, "confidence": "low"},
            "definitely not json",
        ),
        vision_llm=FakeVisionLLM(
            describe_response="A blurry frame.",
            actor_response="also not json",
        ),
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.UNIDENTIFIED


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
