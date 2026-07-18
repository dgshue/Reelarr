"""JSON contract + defensive parsing (carried forward from the original spec)."""

from reelarr.pipeline.prompts import (
    IDENTIFICATION_SYSTEM_PROMPT,
    build_user_content,
    parse_identification,
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
