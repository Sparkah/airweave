"""Meeting entity schemas for AI meeting recording integrations.

Shared entities used by Fathom, Fireflies, tl;dv, Otter, Gong, and other
meeting recording/transcription services.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, computed_field

from airweave.platform.entities._airweave_field import AirweaveField
from airweave.platform.entities._base import BaseEntity


class MeetingSpeaker(BaseModel):
    """Speaker identified in a meeting."""

    id: Optional[str] = None
    name: str
    email: Optional[str] = None


class MeetingActionItem(BaseModel):
    """Action item extracted from a meeting."""

    text: str
    assignee: Optional[str] = None
    due_date: Optional[datetime] = None
    completed: bool = False


class MeetingEntity(BaseEntity):
    """Core meeting metadata - shared across all meeting recording providers.

    This entity represents the meeting itself with metadata about participants,
    timing, and source information.
    """

    meeting_id: str = AirweaveField(
        ...,
        description="Unique meeting ID from the provider",
        is_entity_id=True,
    )
    title: str = AirweaveField(
        ...,
        description="Meeting title or subject",
        embeddable=True,
        is_name=True,
    )

    # Timestamps
    start_time: Optional[datetime] = AirweaveField(
        default=None,
        description="Meeting start time",
        is_created_at=True,
    )
    end_time: Optional[datetime] = AirweaveField(
        default=None,
        description="Meeting end time",
    )
    duration_seconds: Optional[int] = AirweaveField(
        default=None,
        description="Duration in seconds",
    )

    # Participants
    organizer: Optional[str] = AirweaveField(
        default=None,
        description="Meeting organizer name or email",
        embeddable=True,
    )
    speakers: List[MeetingSpeaker] = AirweaveField(
        default_factory=list,
        description="Identified speakers in the meeting",
    )
    participant_count: Optional[int] = AirweaveField(
        default=None,
        description="Number of participants",
    )
    attendees: List[str] = AirweaveField(
        default_factory=list,
        description="List of attendee names or emails",
        embeddable=True,
    )

    # Meeting context
    meeting_platform: Optional[str] = AirweaveField(
        default=None,
        description="Platform used (Zoom, Google Meet, Teams, etc.)",
    )
    meeting_url: Optional[str] = AirweaveField(
        default=None,
        description="Original meeting link",
        unhashable=True,
    )
    recording_url: Optional[str] = AirweaveField(
        default=None,
        description="Link to recording in the provider",
        unhashable=True,
    )

    # Provider info
    provider: str = AirweaveField(
        ...,
        description="Source provider (fathom, fireflies, tldv, otter, gong, etc.)",
    )

    # Additional metadata
    external_id: Optional[str] = AirweaveField(
        default=None,
        description="External calendar event ID if available",
    )
    tags: List[str] = AirweaveField(
        default_factory=list,
        description="Tags or labels applied to the meeting",
        embeddable=True,
    )

    @computed_field(return_type=str)
    def web_url(self) -> str:
        """Browser URL for the meeting."""
        return self.recording_url or self.meeting_url or ""


class MeetingTranscriptEntity(BaseEntity):
    """Full meeting transcript with speaker attribution.

    Contains the complete transcript text, suitable for semantic search
    across meeting content.
    """

    transcript_id: str = AirweaveField(
        ...,
        description="Unique transcript ID",
        is_entity_id=True,
    )
    meeting_id: str = AirweaveField(
        ...,
        description="Parent meeting ID",
    )
    title: str = AirweaveField(
        ...,
        description="Transcript title (usually meeting title)",
        embeddable=True,
        is_name=True,
    )

    # Content
    full_text: str = AirweaveField(
        ...,
        description="Full transcript text with speaker labels",
        embeddable=True,
    )
    word_count: Optional[int] = AirweaveField(
        default=None,
        description="Total words in transcript",
    )
    language: Optional[str] = AirweaveField(
        default=None,
        description="Detected language code (e.g., 'en')",
    )

    # Structured transcript data
    segments: List[Dict[str, Any]] = AirweaveField(
        default_factory=list,
        description="Transcript segments with timestamps and speakers",
    )

    # Timestamps
    created_at_field: Optional[datetime] = AirweaveField(
        default=None,
        description="When the transcript was created",
        is_created_at=True,
    )

    # Provider info
    provider: str = AirweaveField(
        ...,
        description="Source provider",
    )

    # Link to recording
    recording_url: Optional[str] = AirweaveField(
        default=None,
        description="Link to the recording",
        unhashable=True,
    )

    @computed_field(return_type=str)
    def web_url(self) -> str:
        """Browser URL for the transcript."""
        return self.recording_url or ""


class MeetingSummaryEntity(BaseEntity):
    """AI-generated meeting summary with key points and action items.

    Contains structured summary data including highlights, decisions,
    and extracted action items.
    """

    summary_id: str = AirweaveField(
        ...,
        description="Unique summary ID",
        is_entity_id=True,
    )
    meeting_id: str = AirweaveField(
        ...,
        description="Parent meeting ID",
    )
    title: str = AirweaveField(
        ...,
        description="Summary title",
        embeddable=True,
        is_name=True,
    )

    # Summary content
    summary: str = AirweaveField(
        ...,
        description="AI-generated summary text",
        embeddable=True,
    )
    key_points: List[str] = AirweaveField(
        default_factory=list,
        description="Bullet point highlights",
        embeddable=True,
    )
    decisions: List[str] = AirweaveField(
        default_factory=list,
        description="Decisions made during the meeting",
        embeddable=True,
    )
    action_items: List[MeetingActionItem] = AirweaveField(
        default_factory=list,
        description="Extracted action items",
    )
    action_items_text: Optional[str] = AirweaveField(
        default=None,
        description="Action items as searchable text",
        embeddable=True,
    )

    # Topics and sentiment
    topics: List[str] = AirweaveField(
        default_factory=list,
        description="Topics discussed",
        embeddable=True,
    )
    sentiment: Optional[str] = AirweaveField(
        default=None,
        description="Overall meeting sentiment",
    )

    # Timestamps
    created_at_field: Optional[datetime] = AirweaveField(
        default=None,
        description="When the summary was generated",
        is_created_at=True,
    )

    # Provider info
    provider: str = AirweaveField(
        ...,
        description="Source provider",
    )

    # Link to recording
    recording_url: Optional[str] = AirweaveField(
        default=None,
        description="Link to the recording",
        unhashable=True,
    )

    @computed_field(return_type=str)
    def web_url(self) -> str:
        """Browser URL for the summary."""
        return self.recording_url or ""

    def model_post_init(self, __context) -> None:
        """Post-init hook to generate action_items_text from action_items."""
        super().model_post_init(__context)

        if self.action_items and not self.action_items_text:
            self.action_items_text = self._generate_action_items_text()

    def _generate_action_items_text(self) -> str:
        """Generate searchable text from action items."""
        if not self.action_items:
            return ""

        text_parts = []
        for item in self.action_items:
            if item.assignee:
                text_parts.append(f"- {item.text} (assigned to {item.assignee})")
            else:
                text_parts.append(f"- {item.text}")

        return "\n".join(text_parts)


class MeetingHighlightEntity(BaseEntity):
    """Notable moment or clip from a meeting.

    Represents a specific highlight, quote, or important moment
    with timestamp information.
    """

    highlight_id: str = AirweaveField(
        ...,
        description="Unique highlight ID",
        is_entity_id=True,
    )
    meeting_id: str = AirweaveField(
        ...,
        description="Parent meeting ID",
    )
    title: str = AirweaveField(
        ...,
        description="Highlight title or label",
        embeddable=True,
        is_name=True,
    )

    # Content
    text: str = AirweaveField(
        ...,
        description="Highlight text or quote",
        embeddable=True,
    )
    speaker: Optional[str] = AirweaveField(
        default=None,
        description="Speaker who said this",
        embeddable=True,
    )

    # Timing
    timestamp_start: Optional[int] = AirweaveField(
        default=None,
        description="Start time in seconds from meeting start",
    )
    timestamp_end: Optional[int] = AirweaveField(
        default=None,
        description="End time in seconds",
    )

    # Clip URL if available
    clip_url: Optional[str] = AirweaveField(
        default=None,
        description="Direct link to this clip",
        unhashable=True,
    )

    # Classification
    highlight_type: Optional[str] = AirweaveField(
        default=None,
        description="Type: question, decision, action, insight, etc.",
    )

    # Timestamps
    created_at_field: Optional[datetime] = AirweaveField(
        default=None,
        description="When the highlight was created",
        is_created_at=True,
    )

    # Provider info
    provider: str = AirweaveField(
        ...,
        description="Source provider",
    )

    @computed_field(return_type=str)
    def web_url(self) -> str:
        """Browser URL for the highlight."""
        return self.clip_url or ""
