"""Fathom source implementation.

Retrieves AI meeting recordings, transcripts, and summaries from Fathom.video.

Fathom is an AI meeting assistant that records, transcribes, and summarizes
meetings from Zoom, Google Meet, and Microsoft Teams.

Reference:
    https://developers.fathom.ai/quickstart
    https://developers.fathom.ai/sdks/oauth

Entities yielded:
    - MeetingEntity: Core meeting metadata
    - MeetingTranscriptEntity: Full transcript with speaker attribution
    - MeetingSummaryEntity: AI-generated summary with action items
"""

import asyncio
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, before_sleep_log
import logging

from airweave.core.shared_models import RateLimitLevel
from airweave.platform.decorators import source
from airweave.platform.entities._base import BaseEntity, Breadcrumb
from airweave.platform.entities.meeting import (
    MeetingActionItem,
    MeetingEntity,
    MeetingSpeaker,
    MeetingSummaryEntity,
    MeetingTranscriptEntity,
)
from airweave.platform.sources._base import BaseSource
from airweave.platform.sources.retry_helpers import (
    retry_if_rate_limit_or_timeout,
    wait_rate_limit_with_backoff,
)
from airweave.schemas.source_connection import AuthenticationMethod, OAuthType

_logger = logging.getLogger(__name__)


@source(
    name="Fathom",
    short_name="fathom",
    auth_methods=[
        AuthenticationMethod.OAUTH_BROWSER,
        AuthenticationMethod.OAUTH_TOKEN,
        AuthenticationMethod.AUTH_PROVIDER,
    ],
    oauth_type=OAuthType.WITH_ROTATING_REFRESH,
    requires_byoc=True,
    auth_config_class=None,
    config_class="FathomConfig",
    labels=["Meeting Recordings", "Productivity", "AI"],
    supports_continuous=False,
    supports_temporal_relevance=True,
    rate_limit_level=RateLimitLevel.CONNECTION,
)
class FathomSource(BaseSource):
    """Fathom source connector for AI meeting recordings and transcripts.

    Integrates with Fathom.video to retrieve meeting recordings, transcripts,
    and AI-generated summaries. Supports meetings from Zoom, Google Meet, and
    Microsoft Teams that were recorded with Fathom.

    Fathom has a rate limit of 60 API calls per minute. To avoid hitting this,
    we add a small delay between API calls.
    """

    BASE_URL = "https://api.fathom.ai/external/v1"

    # Fathom rate limit: 60 calls/minute. With 3 calls per meeting (list + transcript + summary),
    # we need ~1.5 seconds between meetings to stay safely under the limit.
    # We use 1.2 seconds delay between individual API calls to allow ~50 calls/min.
    RATE_LIMIT_DELAY_SECONDS = 1.2

    @classmethod
    async def create(
        cls, access_token: str, config: Optional[Dict[str, Any]] = None
    ) -> "FathomSource":
        """Create a new Fathom source instance with the provided OAuth access token."""
        instance = cls()
        instance.access_token = access_token
        instance.config = config or {}
        return instance

    @retry(
        stop=stop_after_attempt(10),
        retry=retry_if_rate_limit_or_timeout,
        wait=wait_rate_limit_with_backoff,
        reraise=True,
        before_sleep=before_sleep_log(_logger, logging.WARNING),
    )
    async def _get_with_auth(
        self, client: httpx.AsyncClient, url: str, params: Optional[Dict] = None
    ) -> Dict:
        """Make an authenticated GET request to the Fathom API.

        Includes proactive rate limiting to stay under Fathom's 60 calls/minute limit.
        """
        access_token = await self.get_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}

        response = await client.get(url, headers=headers, params=params)

        if response.status_code == 401:
            self.logger.warning(
                f"Got 401 Unauthorized from Fathom API at {url}, refreshing token..."
            )
            await self.refresh_on_unauthorized()
            access_token = await self.get_access_token()
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get(url, headers=headers, params=params)

        response.raise_for_status()

        # Proactive rate limiting: sleep after each successful call to avoid hitting
        # Fathom's 60 calls/minute limit
        await asyncio.sleep(self.RATE_LIMIT_DELAY_SECONDS)

        return response.json()

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
        """Parse ISO 8601 timestamps into datetime objects."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _extract_speakers(self, meeting_data: Dict) -> List[MeetingSpeaker]:
        """Extract speakers from meeting data."""
        speakers = []
        participants = meeting_data.get("participants", []) or []
        for participant in participants:
            speakers.append(
                MeetingSpeaker(
                    id=participant.get("id"),
                    name=participant.get("name", "Unknown"),
                    email=participant.get("email"),
                )
            )
        return speakers

    def _extract_action_items(self, meeting_data: Dict) -> List[MeetingActionItem]:
        """Extract action items from meeting data."""
        action_items = []
        items = meeting_data.get("action_items", []) or []
        for item in items:
            action_items.append(
                MeetingActionItem(
                    text=item.get("text", ""),
                    assignee=item.get("assignee"),
                    due_date=self._parse_datetime(item.get("due_date")),
                    completed=item.get("completed", False),
                )
            )
        return action_items

    def _format_transcript(self, transcript_data: List[Dict]) -> str:
        """Format transcript segments into readable text with speaker labels."""
        if not transcript_data:
            return ""

        lines = []
        for segment in transcript_data:
            speaker = segment.get("speaker", "Unknown")
            text = segment.get("text", "")
            if text:
                lines.append(f"{speaker}: {text}")

        return "\n\n".join(lines)

    async def _fetch_transcript(
        self, client: httpx.AsyncClient, recording_id: str
    ) -> Optional[Dict]:
        """Fetch transcript for a specific recording.

        OAuth apps cannot use include_transcript parameter on /meetings endpoint,
        so we must fetch transcripts separately via /recordings/{id}/transcript.
        """
        url = f"{self.BASE_URL}/recordings/{recording_id}/transcript"
        try:
            return await self._get_with_auth(client, url)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                self.logger.debug(f"No transcript found for recording {recording_id}")
                return None
            raise

    async def _fetch_summary(
        self, client: httpx.AsyncClient, recording_id: str
    ) -> Optional[Dict]:
        """Fetch summary for a specific recording.

        OAuth apps cannot use include_summary parameter on /meetings endpoint,
        so we must fetch summaries separately via /recordings/{id}/summary.
        """
        url = f"{self.BASE_URL}/recordings/{recording_id}/summary"
        try:
            return await self._get_with_auth(client, url)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                self.logger.debug(f"No summary found for recording {recording_id}")
                return None
            raise

    async def _fetch_meetings(
        self, client: httpx.AsyncClient
    ) -> AsyncGenerator[Dict, None]:
        """Fetch all meetings with pagination.

        Note: OAuth apps cannot use include_transcript or include_summary parameters.
        Transcripts and summaries must be fetched separately per recording.
        """
        url = f"{self.BASE_URL}/meetings"
        params: Dict[str, str] = {}

        # Add date filter if configured
        if self.config.get("created_after"):
            params["created_after"] = self.config["created_after"]

        cursor = None
        page = 0
        consecutive_empty_pages = 0
        max_empty_pages = 3  # Stop after 3 consecutive empty pages

        while True:
            page += 1
            if cursor:
                params["next_cursor"] = cursor

            self.logger.info(f"Fetching Fathom meetings page #{page}")
            data = await self._get_with_auth(client, url, params=params if params else None)

            # Log raw response for first page to debug response format
            if page == 1:
                self.logger.info(f"First page API response keys: {list(data.keys())}")
                # Log a sample of the response (truncated for safety)
                sample = str(data)[:500]
                self.logger.info(f"First page response sample: {sample}")

            meetings = data.get("items", []) or data.get("meetings", []) or data.get("data", []) or []
            self.logger.info(f"Page #{page} returned {len(meetings)} meetings")

            if not meetings:
                consecutive_empty_pages += 1
                self.logger.warning(
                    f"Empty page received ({consecutive_empty_pages}/{max_empty_pages} consecutive)"
                )
                if consecutive_empty_pages >= max_empty_pages:
                    self.logger.info(
                        f"Stopping pagination after {max_empty_pages} consecutive empty pages"
                    )
                    break
            else:
                consecutive_empty_pages = 0  # Reset counter when we get results
                for meeting in meetings:
                    yield meeting

            cursor = data.get("next_cursor")
            if not cursor:
                self.logger.info("No more meeting pages (no next_cursor)")
                break

    async def _generate_meeting_entities(
        self, client: httpx.AsyncClient
    ) -> AsyncGenerator[BaseEntity, None]:
        """Generate all meeting-related entities.

        OAuth apps cannot use include_transcript or include_summary parameters,
        so we fetch transcripts and summaries separately for each meeting.
        """
        async for meeting in self._fetch_meetings(client):
            meeting_id = meeting.get("id") or meeting.get("recording_id")
            if not meeting_id:
                self.logger.warning("Meeting without ID, skipping")
                continue

            title = meeting.get("title") or meeting.get("name") or f"Meeting {meeting_id}"
            start_time = self._parse_datetime(
                meeting.get("date") or meeting.get("start_time") or meeting.get("created_at")
            )
            end_time = self._parse_datetime(meeting.get("end_time"))
            duration = meeting.get("duration_seconds") or meeting.get("duration")

            speakers = self._extract_speakers(meeting)
            attendees = [s.name for s in speakers if s.name]

            recording_url = meeting.get("url") or meeting.get("recording_url")
            if not recording_url and meeting_id:
                recording_url = f"https://fathom.video/recordings/{meeting_id}"

            # Extract organizer - can be a string or a dict with 'name' field
            organizer_data = meeting.get("organizer") or meeting.get("recorded_by")
            if isinstance(organizer_data, dict):
                organizer = organizer_data.get("name") or organizer_data.get("email")
            else:
                organizer = organizer_data

            # 1. Yield MeetingEntity
            meeting_entity = MeetingEntity(
                breadcrumbs=[],
                meeting_id=str(meeting_id),
                title=title,
                start_time=start_time,
                end_time=end_time,
                duration_seconds=duration,
                organizer=organizer,
                speakers=speakers,
                participant_count=len(speakers) if speakers else None,
                attendees=attendees,
                meeting_platform=meeting.get("platform") or meeting.get("meeting_type"),
                meeting_url=meeting.get("meeting_url"),
                recording_url=recording_url,
                provider="fathom",
                external_id=meeting.get("calendar_event_id"),
                tags=meeting.get("tags", []) or [],
            )
            yield meeting_entity

            # Create breadcrumb for child entities
            meeting_breadcrumb = Breadcrumb(
                entity_id=str(meeting_id),
                name=title,
                entity_type=MeetingEntity.__name__,
            )

            # 2. Fetch and yield MeetingTranscriptEntity
            # OAuth apps must fetch transcripts separately via /recordings/{id}/transcript
            self.logger.debug(f"Fetching transcript for meeting {meeting_id}")
            transcript_response = await self._fetch_transcript(client, str(meeting_id))

            if transcript_response:
                # Handle different response formats from the transcript endpoint
                transcript_data = transcript_response.get(
                    "transcript"
                ) or transcript_response.get("segments", transcript_response)

                if isinstance(transcript_data, str):
                    full_text = transcript_data
                    segments = []
                elif isinstance(transcript_data, list):
                    full_text = self._format_transcript(transcript_data)
                    segments = transcript_data
                else:
                    full_text = str(transcript_data) if transcript_data else ""
                    segments = []

                if full_text:
                    word_count = len(full_text.split())

                    transcript_entity = MeetingTranscriptEntity(
                        breadcrumbs=[meeting_breadcrumb],
                        transcript_id=f"{meeting_id}_transcript",
                        meeting_id=str(meeting_id),
                        title=f"Transcript: {title}",
                        full_text=full_text,
                        word_count=word_count,
                        language=transcript_response.get("language", "en"),
                        segments=segments,
                        created_at_field=start_time,
                        provider="fathom",
                        recording_url=recording_url,
                    )
                    yield transcript_entity

            # 3. Fetch and yield MeetingSummaryEntity
            # OAuth apps must fetch summaries separately via /recordings/{id}/summary
            self.logger.debug(f"Fetching summary for meeting {meeting_id}")
            summary_response = await self._fetch_summary(client, str(meeting_id))

            if summary_response:
                # Handle different response formats from the summary endpoint
                summary_data = summary_response.get("summary", summary_response)

                if isinstance(summary_data, str):
                    summary_text = summary_data
                    key_points = []
                    decisions = []
                elif isinstance(summary_data, dict):
                    summary_text = summary_data.get("text") or summary_data.get("overview", "")
                    key_points = summary_data.get("key_points", []) or summary_data.get(
                        "highlights", []
                    )
                    decisions = summary_data.get("decisions", [])
                else:
                    summary_text = str(summary_data) if summary_data else ""
                    key_points = []
                    decisions = []

                if summary_text:
                    # Action items may be in summary response or meeting data
                    action_items = self._extract_action_items(
                        summary_response if isinstance(summary_response, dict) else {}
                    ) or self._extract_action_items(meeting)
                    topics = summary_response.get("topics", []) or meeting.get("topics", []) or []

                    summary_entity = MeetingSummaryEntity(
                        breadcrumbs=[meeting_breadcrumb],
                        summary_id=f"{meeting_id}_summary",
                        meeting_id=str(meeting_id),
                        title=f"Summary: {title}",
                        summary=summary_text,
                        key_points=key_points,
                        decisions=decisions,
                        action_items=action_items,
                        topics=topics,
                        sentiment=summary_response.get("sentiment")
                        if isinstance(summary_response, dict)
                        else None,
                        created_at_field=start_time,
                        provider="fathom",
                        recording_url=recording_url,
                    )
                    yield summary_entity

    async def generate_entities(self) -> AsyncGenerator[BaseEntity, None]:
        """Generate all Fathom entities.

        Yields entities in the following order for each meeting:
          - MeetingEntity (core metadata)
          - MeetingTranscriptEntity (full transcript if available)
          - MeetingSummaryEntity (AI summary if available)
        """
        async with self.http_client() as client:
            async for entity in self._generate_meeting_entities(client):
                yield entity

    async def validate(self) -> bool:
        """Verify Fathom OAuth2 token by pinging the meetings endpoint."""
        return await self._validate_oauth2(
            ping_url=f"{self.BASE_URL}/meetings?limit=1",
            headers={"Accept": "application/json"},
            timeout=10.0,
        )
