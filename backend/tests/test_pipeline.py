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


# --- Year-drift gate (_unambiguous_top): the LLM's year is soft evidence ------

GORGE_2025 = TmdbMatch(tmdb_id=950396, title="The Gorge", year=2025, media_type="movie",
                       popularity=22.99, vote_count=3932)
GORGE_1968 = TmdbMatch(tmdb_id=964734, title="The Gorge", year=1968, media_type="movie",
                       popularity=0.24, vote_count=1)


def make_gorge_pipeline(year, matches):
    return make_pipeline(
        text_llm=FakeTextLLM(
            {"title": "The Gorge", "year": year, "type": "movie", "confidence": "high"}
        ),
        tmdb=FakeTmdb(matches),
    )


async def test_year_drift_within_tolerance_still_auto_adds():
    """The measured 'The Gorge' bug: title right, year hallucinated as 2023
    for the 2025 film. A ±2 drift must not block the auto-add when the only
    same-title rival is both far in year and far in popularity."""
    pipeline = make_gorge_pipeline(2023, [GORGE_2025, GORGE_1968])
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.AUTO_ADD
    assert result.match is GORGE_2025


async def test_year_gap_beyond_tolerance_needs_confirmation():
    """A unique exact-title match with a wildly wrong year still ranks first,
    but goes to confirmation rather than silently overriding the LLM's claim."""
    pipeline = make_gorge_pipeline(1990, [GORGE_2025])
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_CONFIRMATION
    assert result.candidates[0] is GORGE_2025


async def test_drifted_year_cannot_pick_between_same_title_rivals():
    """The measured 'Digger' rerun that returned year=2023: Digger (2021) is
    nearer the claimed year but Digger (2026) is ~30x more popular — the two
    axes disagree, so a hallucinated year must not silently pick the 2021
    film. This is the case the old exact-year gate got wrong."""
    digger_2021 = TmdbMatch(tmdb_id=656272, title="Digger", year=2021,
                            media_type="movie", popularity=0.69, vote_count=28)
    digger_2026 = TmdbMatch(tmdb_id=1248832, title="Digger", year=2026,
                            media_type="movie", popularity=20.76, vote_count=0)
    pipeline = make_pipeline(
        text_llm=FakeTextLLM(
            {"title": "Digger", "year": 2023, "type": "movie", "confidence": "high"}
        ),
        # Ranked order (year proximity puts 2021 first) — the gate must still balk.
        tmdb=FakeTmdb([digger_2021, digger_2026]),
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_CONFIRMATION


async def test_null_year_with_same_title_rivals_needs_confirmation():
    """'Digger' with year=None: three real films named Digger and no year
    evidence — popularity alone must never auto-add (votes=0 on the top film;
    it is popular because it is new, not because it is confirmed right)."""
    diggers = [
        TmdbMatch(tmdb_id=1248832, title="Digger", year=2026, media_type="movie",
                  popularity=20.76),
        TmdbMatch(tmdb_id=656272, title="Digger", year=2021, media_type="movie",
                  popularity=0.69),
        TmdbMatch(tmdb_id=290686, title="Digger", year=1993, media_type="movie",
                  popularity=0.59),
    ]
    pipeline = make_pipeline(
        text_llm=FakeTextLLM(
            {"title": "Digger", "year": None, "type": "movie", "confidence": "high"}
        ),
        tmdb=FakeTmdb(diggers),
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_CONFIRMATION
    assert result.candidates[0].tmdb_id == 1248832  # best guess still offered first


async def test_exact_year_hit_still_rules_out_same_title_rivals():
    """Unchanged from the original gate: a gap-0 year hit disambiguates
    same-title films at other years (Heat 1995 vs the 2013 remake)."""
    heat_2013 = TmdbMatch(tmdb_id=2, title="Heat", year=2013, media_type="movie",
                          popularity=50.0)  # more popular must NOT matter at gap 0
    pipeline = make_pipeline(tmdb=FakeTmdb([HEAT, heat_2013]))
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.AUTO_ADD
    assert result.match is HEAT


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


# --- Multi-title (listicle / versus) flow, spec §5.4 ---------------------------

INCEPTION = TmdbMatch(tmdb_id=27205, title="Inception", year=2010, media_type="movie")
MEMENTO = TmdbMatch(tmdb_id=77, title="Memento", year=2000, media_type="movie")
PRIMER = TmdbMatch(tmdb_id=14337, title="Primer", year=2004, media_type="movie")
BLADE_RUNNER = TmdbMatch(tmdb_id=78, title="Blade Runner", year=1982, media_type="movie")
BLADE_RUNNER_2049 = TmdbMatch(tmdb_id=335984, title="Blade Runner 2049", year=2017, media_type="movie")

MULTI_TMDB = FakeTmdb(
    matches_by_query={
        "Inception": [INCEPTION],
        "Memento": [MEMENTO],
        "Primer": [PRIMER],
        "Blade Runner": [BLADE_RUNNER],
        "Blade Runner 2049": [BLADE_RUNNER_2049],
    }
)


def _title(name, year, confidence="high"):
    return {"title": name, "year": year, "type": "movie", "confidence": confidence}


def make_listicle_pipeline(caption, *llm_responses, **kwargs):
    defaults = dict(
        resolver=FakeResolver(metadata=ClipMetadata(platform="tiktok", description=caption)),
        text_llm=FakeTextLLM(*llm_responses),
        stt=FakeStt(""),
        tmdb=MULTI_TMDB,
        enable_vision=False,
    )
    defaults.update(kwargs)
    return IdentificationPipeline(**defaults)


async def test_listicle_caption_offers_multi_select_at_tier1():
    pipeline = make_listicle_pipeline(
        "3 mind-bending movies: Inception, Memento, Primer",
        {"title": None, "confidence": "low"},  # tier 1 single-title call
        {   # multi-title extraction
            "post_type": "listicle",
            "stated_count": 3,
            "titles": [_title("Inception", 2010), _title("Memento", 2000), _title("Primer", 2004)],
        },
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_MULTI_SELECT
    assert result.post_type == "listicle"
    assert result.stated_count == 3
    assert [c.match.title for c in result.multi_candidates] == ["Inception", "Memento", "Primer"]
    assert result.resolved_tier == "metadata"
    # Fast path: resolved from the caption alone — tiers 2/3 never ran
    assert pipeline.resolver.calls == ["metadata"]
    assert not result.truncated
    assert result.unresolved_titles == []


async def test_single_subject_caption_never_triggers_multi():
    """The proven Heat-with-distractors path: hint-free caption, one LLM call,
    auto-add — byte-for-byte today's behavior."""
    pipeline = make_pipeline()
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.AUTO_ADD
    assert len(pipeline.text_llm.calls) == 1  # no multi-title call was made


async def test_hinted_but_single_classification_falls_through():
    """'Heat vs every other heist movie' hints versus, but the model classifies
    it single-subject -> the normal single-title flow proceeds untouched."""
    pipeline = make_pipeline(
        resolver=FakeResolver(
            metadata=ClipMetadata(
                platform="tiktok", description="Heat vs every other heist movie — not close"
            )
        ),
        text_llm=FakeTextLLM(
            {"title": "Heat", "year": 1995, "type": "movie", "confidence": "high"},
            {"post_type": "single", "stated_count": None, "titles": [_title("Heat", 1995)]},
        ),
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.AUTO_ADD
    assert result.match is HEAT
    assert len(pipeline.text_llm.calls) == 2  # single + multi, nothing more


async def test_versus_post_offers_both():
    pipeline = make_listicle_pipeline(
        "Blade Runner vs Blade Runner 2049 — which ending hits harder?",
        {"title": None, "confidence": "medium"},
        {
            "post_type": "versus",
            "stated_count": None,
            "titles": [_title("Blade Runner", 1982), _title("Blade Runner 2049", 2017)],
        },
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_MULTI_SELECT
    assert result.post_type == "versus"
    assert [c.match.tmdb_id for c in result.multi_candidates] == [78, 335984]


async def test_multi_stated_count_shortfall_and_unresolved_surface():
    """Post claims 10, model finds 3, TMDB matches only 2 -> the shortfall and
    the unmatched title are reported, not silently dropped (spec §1/§5.4)."""
    pipeline = make_listicle_pipeline(
        "top 10 horror films of the decade",
        {"title": None, "confidence": "low"},
        {
            "post_type": "listicle",
            "stated_count": 10,
            "titles": [
                _title("Inception", 2010),
                _title("Memento", 2000),
                _title("Some Obscure Festival Film", 2021),
            ],
        },
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_MULTI_SELECT
    assert result.stated_count == 10
    assert len(result.multi_candidates) == 2
    assert result.unresolved_titles == ["Some Obscure Festival Film"]


async def test_multi_cap_truncates_and_flags():
    titles = [_title(f"Movie {i}", 2000 + i) for i in range(12)]
    tmdb = FakeTmdb(
        matches_by_query={
            f"Movie {i}": [TmdbMatch(tmdb_id=1000 + i, title=f"Movie {i}", year=2000 + i, media_type="movie")]
            for i in range(12)
        }
    )
    pipeline = make_listicle_pipeline(
        "ranking every slasher movie ever",
        {"title": None, "confidence": "low"},
        {"post_type": "listicle", "stated_count": 50, "titles": titles},
        tmdb=tmdb,
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_MULTI_SELECT
    assert len(result.multi_candidates) == 10  # default cap
    assert result.truncated


async def test_multi_dedupes_titles_resolving_to_same_entry():
    pipeline = make_listicle_pipeline(
        "3 movies to rewatch: Inception, Inception again, Memento",
        {"title": None, "confidence": "low"},
        {
            "post_type": "listicle",
            "stated_count": 3,
            "titles": [
                _title("Inception", 2010),
                {"title": "Inception Again", "year": 2010, "type": "movie", "confidence": "low"},
                _title("Memento", 2000),
            ],
        },
        tmdb=FakeTmdb(
            matches_by_query={
                "Inception": [INCEPTION],
                "Inception Again": [INCEPTION],  # fuzzy TMDB hit on the same entry
                "Memento": [MEMENTO],
            }
        ),
    )
    result = await pipeline.run(URL)
    assert [c.match.tmdb_id for c in result.multi_candidates] == [27205, 77]


async def test_multi_per_title_confidence_and_fuzzy_demotion():
    """Per-title confidence survives; a 'high' whose TMDB top hit isn't an
    exact title match is demoted to medium (a fuzzy hit is a guess)."""
    pipeline = make_listicle_pipeline(
        "3 movies: Inception, Memento, and one more",
        {"title": None, "confidence": "low"},
        {
            "post_type": "listicle",
            "stated_count": 3,
            "titles": [
                _title("Inception", 2010, "high"),
                _title("Memento", 2000, "low"),
                _title("Primers", 2004, "high"),  # resolves fuzzily to "Primer"
            ],
        },
        tmdb=FakeTmdb(
            matches_by_query={
                "Inception": [INCEPTION],
                "Memento": [MEMENTO],
                "Primers": [PRIMER],
            }
        ),
    )
    result = await pipeline.run(URL)
    confidences = {c.match.title: c.confidence for c in result.multi_candidates}
    assert confidences == {"Inception": "high", "Memento": "low", "Primer": "medium"}


async def test_multi_never_auto_adds_even_all_high():
    pipeline = make_listicle_pipeline(
        "2 movies: Inception, Memento",
        {"title": None, "confidence": "low"},
        {
            "post_type": "listicle",
            "stated_count": 2,
            "titles": [_title("Inception", 2010), _title("Memento", 2000)],
        },
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_MULTI_SELECT  # never AUTO_ADD


async def test_hinted_countdown_retries_multi_with_transcript():
    """Caption only counts ('top 10...'); the titles are in the voiceover.
    The multi pass re-runs at tier 2 with the transcript."""
    text_llm = FakeTextLLM(
        {"title": None, "confidence": "low"},                        # tier 1 single
        {"post_type": "listicle", "stated_count": 10, "titles": []},  # tier 1 multi: caption names none
        {"title": None, "confidence": "low"},                        # tier 2 single
        {   # tier 2 multi, now with the transcript
            "post_type": "listicle",
            "stated_count": 10,
            "titles": [_title("Inception", 2010), _title("Memento", 2000)],
        },
    )
    pipeline = make_listicle_pipeline(
        "top 10 mind-bending movies",
        stt=FakeStt("Number ten, Inception. Number nine, Memento."),
        text_llm=text_llm,
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_MULTI_SELECT
    assert result.resolved_tier == "transcript"
    assert "AUDIO TRANSCRIPT:" in text_llm.calls[3][1]
    # The caption-count prior is fed into the multi prompt
    assert "claims this post covers 10 titles" in text_llm.calls[1][1]


async def test_unhinted_listicle_caught_by_last_chance_pass():
    """No numeric/keyword hint in the caption — the multi pass still runs as a
    last resort before declaring UNIDENTIFIED."""
    pipeline = make_listicle_pipeline(
        "these will mess with your mind: Inception, Memento",
        {"title": None, "confidence": "low"},  # tier 1 single (repeats for tier 2)
        {
            "post_type": "listicle",
            "stated_count": None,
            "titles": [_title("Inception", 2010), _title("Memento", 2000)],
        },
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_MULTI_SELECT
    assert len(result.multi_candidates) == 2


async def test_multi_llm_failure_degrades_to_single_flow():
    """A timeout/exception in the additive multi pass must not kill the run."""

    class ExplodingSecondCallLLM:
        def __init__(self):
            self.calls = 0

        async def complete(self, system, user):
            self.calls += 1
            if self.calls == 1:
                return '{"title": "Heat", "year": 1995, "type": "movie", "confidence": "high"}'
            raise RuntimeError("LLM timeout")

    pipeline = make_pipeline(
        resolver=FakeResolver(
            metadata=ClipMetadata(platform="tiktok", description="top 5 heist movies: Heat and more")
        ),
        text_llm=ExplodingSecondCallLLM(),
    )
    result = await pipeline.run(URL)
    # Multi pass exploded -> normal single flow continues with tier-1 Heat
    assert result.outcome == PipelineOutcome.AUTO_ADD
    assert result.match is HEAT


async def test_multi_requires_two_resolved_titles():
    """If TMDB resolution leaves fewer than 2 titles, multi-select is not
    offered. The one findable title falls back to the single flow — capped at
    medium (confirmation), never a silent auto-add of a fraction of a list."""
    pipeline = make_listicle_pipeline(
        "2 movies: Inception and something obscure",
        {"title": None, "confidence": "low"},
        {
            "post_type": "listicle",
            "stated_count": 2,
            "titles": [_title("Inception", 2010), _title("Unfindable Film", 1999)],
        },
        tmdb=FakeTmdb(matches_by_query={"Inception": [INCEPTION]}),
    )
    result = await pipeline.run(URL)
    assert result.outcome == PipelineOutcome.NEEDS_CONFIRMATION
    assert result.identification.confidence == "medium"
    assert [c.title for c in result.candidates] == ["Inception"]


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
