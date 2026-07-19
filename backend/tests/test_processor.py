"""RequestProcessor multi-select flow (spec §5.4) — DB-backed selection state.

Uses a real in-memory SQLite session so the "selection state persists on the
request row, not in memory" requirement is what's actually under test.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from reelarr.db import Base
from reelarr.fulfillment.base import FulfillmentResult, FulfillmentStatus
from reelarr.intake.base import (
    ConfirmationCandidate,
    InboundLink,
    IntakeChannel,
    MultiSelectOption,
)
from reelarr.models.requests import BlocklistEntry, MediaRequest, RequestStatus
from reelarr.pipeline.identify import (
    MultiTitleCandidate,
    PipelineOutcome,
    PipelineResult,
)
from reelarr.pipeline.tmdb import TmdbMatch
from reelarr.services.processor import RequestProcessor

pytestmark = pytest.mark.asyncio

URL = "https://www.tiktok.com/@filmidol/video/999"

INCEPTION = TmdbMatch(tmdb_id=27205, title="Inception", year=2010, media_type="movie")
MEMENTO = TmdbMatch(tmdb_id=77, title="Memento", year=2000, media_type="movie")
PRIMER = TmdbMatch(tmdb_id=14337, title="Primer", year=2004, media_type="movie")

MULTI_RESULT = PipelineResult(
    outcome=PipelineOutcome.NEEDS_MULTI_SELECT,
    resolved_tier="metadata",
    multi_candidates=[
        MultiTitleCandidate(match=INCEPTION, confidence="high"),
        MultiTitleCandidate(match=MEMENTO, confidence="high"),
        MultiTitleCandidate(match=PRIMER, confidence="low"),
    ],
    post_type="listicle",
    stated_count=5,
    unresolved_titles=["Coherence"],
)


class FakePipeline:
    def __init__(self, result: PipelineResult) -> None:
        self.result = result

    async def run(self, url: str) -> PipelineResult:
        return self.result


class FakeFulfillment:
    def __init__(self) -> None:
        self.fulfilled: list[TmdbMatch] = []

    async def fulfill(self, match: TmdbMatch) -> FulfillmentResult:
        self.fulfilled.append(match)
        return FulfillmentResult(status=FulfillmentStatus.ADDED, detail="added")

    async def test(self) -> dict:
        return {"ok": True}


class FakeChannel(IntakeChannel):
    channel_type = "telegram"

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[tuple[str, str]] = []
        self.multi_sends: list[tuple[str, str, list[MultiSelectOption]]] = []
        self.multi_updates: list[tuple[str | None, list[MultiSelectOption], bool]] = []

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def test(self) -> dict:
        return {"ok": True}

    async def send_text(self, chat_ref: str, text: str) -> None:
        self.texts.append((chat_ref, text))

    async def send_confirmation(
        self, chat_ref: str, prompt: str, candidates: list[ConfirmationCandidate]
    ) -> None: ...

    async def send_multi_select(
        self, chat_ref: str, prompt: str, options: list[MultiSelectOption]
    ) -> str | None:
        self.multi_sends.append((chat_ref, prompt, options))
        return "msg-1"

    async def update_multi_select(
        self, chat_ref: str, message_ref: str | None, options: list[MultiSelectOption],
        confirm_all: bool = False,
    ) -> None:
        self.multi_updates.append((message_ref, options, confirm_all))


@pytest.fixture
def db_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def make_processor(db_factory, result: PipelineResult = MULTI_RESULT):
    channel = FakeChannel()
    fulfillment = FakeFulfillment()
    processor = RequestProcessor(
        db_factory=db_factory,
        pipeline=FakePipeline(result),
        fulfillment=fulfillment,
        channels={"telegram": channel},
        fulfillment_target="direct",
    )
    return processor, channel, fulfillment


LINK = InboundLink(url=URL, channel="telegram", chat_ref="42", user_ref="7")


async def test_multi_result_persists_candidates_and_sends_prompt(db_factory):
    processor, channel, _ = make_processor(db_factory)
    await processor.handle_link(LINK)

    db = db_factory()
    request = db.execute(select(MediaRequest)).scalar_one()
    assert request.status == RequestStatus.PENDING_CONFIRMATION
    assert [c["selected"] for c in request.candidates] == [False, False, False]
    assert [c["confidence"] for c in request.candidates] == ["high", "high", "low"]
    db.close()

    (chat_ref, prompt, options) = channel.multi_sends[0]
    assert chat_ref == "42"
    # Shortfall against the caption's stated count + unmatched titles surfaced
    assert "claims 5" in prompt and "identified 3" in prompt
    assert "Coherence" in prompt
    assert [o.label for o in options] == [
        "Inception (2010) — movie",
        "Memento (2000) — movie",
        "Primer (2004) — movie (guess)",  # per-title confidence surfaced
    ]


async def test_toggle_flips_db_state_and_rerenders(db_factory):
    processor, channel, _ = make_processor(db_factory)
    await processor.handle_link(LINK)
    request_id = channel.multi_sends[0][2][0].request_id

    await processor.handle_multi_select(request_id, "toggle", [2], "42", "msg-1")
    db = db_factory()
    request = db.get(MediaRequest, request_id)
    assert [c["selected"] for c in request.candidates] == [False, True, False]
    db.close()
    message_ref, options, confirm_all = channel.multi_updates[-1]
    assert message_ref == "msg-1"
    assert [o.selected for o in options] == [False, True, False]
    assert confirm_all is False

    # Toggle back off
    await processor.handle_multi_select(request_id, "toggle", [2], "42", "msg-1")
    db = db_factory()
    request = db.get(MediaRequest, request_id)
    assert [c["selected"] for c in request.candidates] == [False, False, False]
    db.close()


async def test_selection_survives_processor_restart(db_factory):
    """Toggles land on the request row, so a fresh processor (new process,
    same DB) picks the selection up untouched."""
    processor, channel, _ = make_processor(db_factory)
    await processor.handle_link(LINK)
    request_id = channel.multi_sends[0][2][0].request_id
    await processor.handle_multi_select(request_id, "toggle", [1], "42", "msg-1")

    # "Restart": brand-new processor + channel over the same database
    processor2, channel2, fulfillment2 = make_processor(db_factory)
    await processor2.handle_multi_select(request_id, "confirm", None, "42", "msg-1")
    assert [m.tmdb_id for m in fulfillment2.fulfilled] == [27205]


async def test_confirm_with_nothing_selected_nudges(db_factory):
    processor, channel, fulfillment = make_processor(db_factory)
    await processor.handle_link(LINK)
    request_id = channel.multi_sends[0][2][0].request_id

    await processor.handle_multi_select(request_id, "confirm", None, "42", "msg-1")
    assert fulfillment.fulfilled == []
    assert "Nothing selected" in channel.texts[-1][1]
    db = db_factory()
    assert db.get(MediaRequest, request_id).status == RequestStatus.PENDING_CONFIRMATION
    db.close()


async def test_confirm_creates_one_row_per_selected_title(db_factory):
    processor, channel, fulfillment = make_processor(db_factory)
    await processor.handle_link(LINK)
    request_id = channel.multi_sends[0][2][0].request_id

    await processor.handle_multi_select(request_id, "toggle", [1], "42", "msg-1")
    await processor.handle_multi_select(request_id, "toggle", [3], "42", "msg-1")
    await processor.handle_multi_select(request_id, "confirm", None, "42", "msg-1")

    assert [m.tmdb_id for m in fulfillment.fulfilled] == [27205, 14337]
    db = db_factory()
    rows = db.execute(select(MediaRequest).order_by(MediaRequest.id)).scalars().all()
    # Parent row = first selection; sibling row for the second title
    assert len(rows) == 2
    assert rows[0].id == request_id
    assert (rows[0].title, rows[0].status) == ("Inception", RequestStatus.FULFILLED)
    assert (rows[1].title, rows[1].status) == ("Primer", RequestStatus.FULFILLED)
    assert rows[1].url == URL and rows[1].source_channel == "telegram"
    assert rows[1].confidence == "low"  # per-title confidence carried onto its row
    db.close()
    # One summary message, not one per title
    summary = channel.texts[-1][1]
    assert "Added 2 titles" in summary
    assert "Inception" in summary and "Primer" in summary


async def test_add_all_reprompts_then_confirm_all_fulfills_everything(db_factory):
    processor, channel, fulfillment = make_processor(db_factory)
    await processor.handle_link(LINK)
    request_id = channel.multi_sends[0][2][0].request_id

    # "Add all" must re-prompt (spec §5.4), not fire immediately
    await processor.handle_multi_select(request_id, "all", None, "42", "msg-1")
    assert fulfillment.fulfilled == []
    assert channel.multi_updates[-1][2] is True  # confirm_all keyboard

    # "Back" returns to the selection keyboard
    await processor.handle_multi_select(request_id, "back", None, "42", "msg-1")
    assert channel.multi_updates[-1][2] is False

    await processor.handle_multi_select(request_id, "all", None, "42", "msg-1")
    await processor.handle_multi_select(request_id, "confirm_all", None, "42", "msg-1")
    assert [m.tmdb_id for m in fulfillment.fulfilled] == [27205, 77, 14337]
    db = db_factory()
    rows = db.execute(select(MediaRequest)).scalars().all()
    assert len(rows) == 3
    assert all(r.status == RequestStatus.FULFILLED for r in rows)
    db.close()


async def test_replace_sets_whole_selection(db_factory):
    """Native multi-select platforms (Discord) and numbered replies (WhatsApp)
    submit the full set at once via 'replace'."""
    processor, channel, fulfillment = make_processor(db_factory)
    await processor.handle_link(LINK)
    request_id = channel.multi_sends[0][2][0].request_id

    await processor.handle_multi_select(request_id, "replace", [2, 3], "42", None)
    await processor.handle_multi_select(request_id, "confirm", None, "42", None)
    assert [m.tmdb_id for m in fulfillment.fulfilled] == [77, 14337]


async def test_none_dismisses_and_blocklists(db_factory):
    processor, channel, fulfillment = make_processor(db_factory)
    await processor.handle_link(LINK)
    request_id = channel.multi_sends[0][2][0].request_id

    await processor.handle_multi_select(request_id, "none", None, "42", "msg-1")
    assert fulfillment.fulfilled == []
    db = db_factory()
    assert db.get(MediaRequest, request_id).status == RequestStatus.DISMISSED
    assert db.execute(select(BlocklistEntry)).scalar_one().url == URL
    db.close()


async def test_stale_taps_after_fulfillment_are_ignored(db_factory):
    processor, channel, fulfillment = make_processor(db_factory)
    await processor.handle_link(LINK)
    request_id = channel.multi_sends[0][2][0].request_id

    await processor.handle_multi_select(request_id, "toggle", [1], "42", "msg-1")
    await processor.handle_multi_select(request_id, "confirm", None, "42", "msg-1")
    fulfilled_before = list(fulfillment.fulfilled)

    # Old keyboard is still on screen — a second tap must be a no-op
    await processor.handle_multi_select(request_id, "confirm", None, "42", "msg-1")
    await processor.handle_multi_select(request_id, "toggle", [2], "42", "msg-1")
    assert fulfillment.fulfilled == fulfilled_before
