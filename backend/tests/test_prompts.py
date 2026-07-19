"""JSON contract + defensive parsing (carried forward from the original spec)."""

from reelarr.pipeline.prompts import (
    IDENTIFICATION_SYSTEM_PROMPT,
    build_evidence_content,
    build_multi_title_content,
    build_user_content,
    detect_listicle_signal,
    parse_actor_guesses,
    parse_evidence_identification,
    parse_identification,
    parse_multi_title_extraction,
)


def test_system_prompt_carries_forward_contract():
    assert '"title": string|null' in IDENTIFICATION_SYSTEM_PROMPT
    assert '"confidence": "high"|"medium"|"low"' in IDENTIFICATION_SYSTEM_PROMPT
    assert "#fyp" in IDENTIFICATION_SYSTEM_PROMPT


def test_parse_clean_json():
    ident = parse_identification(
        '{"title": "Heat", "year": 1995, "type": "movie", "confidence": "high"}'
    )
    assert ident.title == "Heat"
    assert ident.year == 1995
    assert ident.media_type == "movie"
    assert ident.confidence == "high"
    assert not ident.is_unknown


def test_parse_strips_markdown_fences():
    raw = '```json\n{"title": "Severance", "year": 2022, "type": "tv", "confidence": "medium"}\n```'
    ident = parse_identification(raw)
    assert ident.title == "Severance"
    assert ident.media_type == "tv"


def test_parse_json_embedded_in_prose():
    raw = 'Sure! Here is the answer: {"title": "Dune", "year": 2021, "type": "movie", "confidence": "low"} Hope that helps.'
    ident = parse_identification(raw)
    assert ident.title == "Dune"
    assert ident.confidence == "low"


def test_parse_garbage_falls_back_to_unknown():
    for raw in ("", "not json at all", "{broken json", '["a","list"]', "null"):
        ident = parse_identification(raw)
        assert ident.is_unknown
        assert ident.confidence == "low"


def test_parse_null_title_is_unknown():
    ident = parse_identification('{"title": null, "year": null, "type": null, "confidence": "low"}')
    assert ident.is_unknown


def test_parse_coerces_string_year_and_invalid_fields():
    ident = parse_identification(
        '{"title": "Alien", "year": "1979", "type": "documentary", "confidence": "certain"}'
    )
    assert ident.year == 1979
    assert ident.media_type is None  # invalid type discarded
    assert ident.confidence == "low"  # invalid confidence coerced


def test_build_user_content_labels_sections():
    content = build_user_content(
        caption="what movie is this",
        hashtags=["#film", "#deniro"],
        top_comments=["It's Heat (1995)!"],
        transcript="You want to be making moves on the street...",
    )
    assert "CAPTION:" in content
    assert "HASHTAGS:" in content
    assert "TOP COMMENTS:" in content
    assert "AUDIO TRANSCRIPT:" in content
    assert "It's Heat (1995)!" in content


def test_build_user_content_empty():
    assert "(no metadata available)" in build_user_content()


# --- Tier 3: actor-guess parsing ---------------------------------------------


def test_parse_actor_guesses_clean_and_fenced():
    raw = '{"actors": [{"name": "Bradley Cooper", "confidence": "likely"}]}'
    assert parse_actor_guesses(raw) == ["Bradley Cooper"]
    fenced = '```json\n{"actors": [{"name": "Zooey Deschanel", "confidence": "certain"}]}\n```'
    assert parse_actor_guesses(fenced) == ["Zooey Deschanel"]


def test_parse_actor_guesses_filters_unsure_and_garbage():
    raw = (
        '{"actors": [{"name": "A", "confidence": "unsure"}, {"name": "", "confidence": "likely"},'
        ' {"confidence": "likely"}, {"name": "B", "confidence": "likely"}, "junk"]}'
    )
    assert parse_actor_guesses(raw) == ["B"]
    assert parse_actor_guesses(raw, include_unsure=True) == ["A", "B"]
    for bad in ("", "not json", '{"actors": "nope"}', "[]"):
        assert parse_actor_guesses(bad) == []


# --- Tier 3: evidence identification parsing -----------------------------------


def test_parse_evidence_identification_full():
    raw = (
        '{"candidates": [{"title": "Failure to Launch", "year": 2006, "type": "movie",'
        ' "confidence": "medium"}], "character_names": ["Tripp\'s", "Ace", "Ace"]}'
    )
    candidates, names = parse_evidence_identification(raw)
    assert candidates[0].title == "Failure to Launch"
    assert candidates[0].year == 2006
    assert names == ["Tripp", "Ace"]  # possessive stripped, deduped


def test_parse_evidence_identification_strips_think_block():
    raw = (
        "<think>The subtitles mention Tripp... hmm {not json}</think>\n"
        '{"candidates": [], "character_names": ["Tripp"]}'
    )
    candidates, names = parse_evidence_identification(raw)
    assert candidates == []
    assert names == ["Tripp"]


def test_parse_evidence_identification_garbage_is_safe():
    for raw in ("", "not json", '{"candidates": "x", "character_names": 3}', "null"):
        candidates, names = parse_evidence_identification(raw)
        assert candidates == []
        assert names == []


def test_parse_evidence_identification_caps_candidates_and_drops_unknown():
    raw = (
        '{"candidates": ['
        '{"title": "A", "type": "movie", "confidence": "low"},'
        '{"title": null, "type": "movie", "confidence": "high"},'
        '{"title": "B", "type": "tv", "confidence": "low"},'
        '{"title": "C", "type": "movie", "confidence": "low"},'
        '{"title": "D", "type": "movie", "confidence": "low"}],'
        ' "character_names": []}'
    )
    candidates, _ = parse_evidence_identification(raw)
    assert [c.title for c in candidates] == ["A", "B", "C"]


# --- Multi-title (listicle) extraction, spec §5.4 -------------------------------


def test_detect_listicle_signal_patterns():
    # Numeric and word-number counts
    assert detect_listicle_signal("top 10 horror films of the decade") == (True, 10)
    assert detect_listicle_signal("5 mind-bending movies you need to watch") == (True, 5)
    assert detect_listicle_signal("five underrated sci-fi films") == (True, 5)
    assert detect_listicle_signal("my top5 comfort shows") == (True, 5)
    # Keyword-only hints: no count prior
    assert detect_listicle_signal("ranking every A24 horror movie") == (True, None)
    assert detect_listicle_signal("Blade Runner vs Blade Runner 2049") == (True, None)
    assert detect_listicle_signal("Alien versus Aliens, pick one") == (True, None)


def test_detect_listicle_signal_no_false_positives():
    # The proven single-subject cases must not trigger the multi path
    assert detect_listicle_signal("This scene from Heat is unmatched") == (False, None)
    assert detect_listicle_signal("best deniro scene #movie") == (False, None)
    assert detect_listicle_signal(None) == (False, None)
    assert detect_listicle_signal("") == (False, None)
    # "2049" is a title year, not a count; "top tier" has no number
    assert detect_listicle_signal("Blade Runner 2049 is a masterpiece") == (False, None)
    assert detect_listicle_signal("top tier acting right here") == (False, None)


def test_detect_listicle_signal_count_bounds():
    # A count needs 2..100 to be a useful prior; the hint itself still fires
    assert detect_listicle_signal("top 1 movie of all time") == (True, None)
    assert detect_listicle_signal("top 999 movies") == (True, None)


def test_parse_multi_title_extraction_full():
    raw = (
        '{"post_type": "listicle", "stated_count": 5, "titles": ['
        '{"title": "Inception", "year": 2010, "type": "movie", "confidence": "high"},'
        '{"title": "Memento", "year": "2000", "type": "movie", "confidence": "medium"}]}'
    )
    extraction = parse_multi_title_extraction(raw)
    assert extraction.post_type == "listicle"
    assert extraction.stated_count == 5
    assert [t.title for t in extraction.titles] == ["Inception", "Memento"]
    assert extraction.titles[1].year == 2000  # string year coerced
    assert extraction.titles[1].confidence == "medium"


def test_parse_multi_title_extraction_dedupes_and_drops_unknown():
    raw = (
        '{"post_type": "listicle", "stated_count": null, "titles": ['
        '{"title": "Enemy", "year": 2013, "type": "movie", "confidence": "high"},'
        '{"title": "enemy", "year": 2013, "type": "movie", "confidence": "low"},'
        '{"title": null, "type": "movie", "confidence": "high"},'
        '{"title": "  ", "type": "movie", "confidence": "high"}]}'
    )
    extraction = parse_multi_title_extraction(raw)
    assert [t.title for t in extraction.titles] == ["Enemy"]


def test_parse_multi_title_extraction_garbage_is_safe():
    for raw in ("", "not json", '{"titles": "x"}', "null", '{"post_type": "rant"}'):
        extraction = parse_multi_title_extraction(raw)
        assert extraction.titles == []
        assert extraction.post_type in ("unknown",)
        assert extraction.stated_count is None


def test_parse_multi_title_extraction_bogus_counts_discarded():
    for count in (1, 0, -3, 999, "lots"):
        raw = f'{{"post_type": "listicle", "stated_count": {count!r}, "titles": []}}'.replace("'", '"')
        assert parse_multi_title_extraction(raw).stated_count is None


def test_build_multi_title_content_includes_count_prior():
    content = build_multi_title_content(
        caption="top 10 horror films", transcript="Number ten, Hereditary.", stated_count=10
    )
    assert "CAPTION:" in content
    assert "AUDIO TRANSCRIPT:" in content
    assert "claims this post covers 10 titles" in content
    assert "do not invent titles" in content
    # No prior -> no NOTE section
    assert "claims this post covers" not in build_multi_title_content(caption="x")


def test_build_evidence_content_labels_sections():
    content = build_evidence_content(
        caption="#movie #fyp",
        transcript="Ace told me everything.",
        frame_descriptions=["ON-SCREEN TEXT: Amy is Tripp's first love", "  "],
    )
    assert "CAPTION:" in content
    assert "AUDIO TRANSCRIPT:" in content
    assert "VISUAL DESCRIPTION OF FRAMES" in content
    assert "Tripp's first love" in content
    assert build_evidence_content() == "(no evidence available)"
