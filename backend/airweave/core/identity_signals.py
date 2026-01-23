"""Identity signal extraction from entity data.

This module provides patterns and utilities for extracting identity-relevant
information (emails, names) from entity data for cross-source matching.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

# Email field patterns - common field names that typically contain email addresses
EMAIL_FIELD_PATTERNS: List[str] = [
    "email",
    "email_address",
    "emailaddress",
    "e_mail",
    "mail",
    "sender_email",
    "sender",
    "from_email",
    "from",
    "author_email",
    "user_email",
    "contact_email",
    "primary_email",
    "work_email",
    "personal_email",
    "owner_email",
    "assignee_email",
    "reporter_email",
    "creator_email",
    "email_addr",
]

# Name field patterns - common field names that typically contain person names
NAME_FIELD_PATTERNS: List[str] = [
    "name",
    "full_name",
    "fullname",
    "display_name",
    "displayname",
    "real_name",
    "realname",
    "sender_name",
    "from_name",
    "author_name",
    "author",
    "user_name",
    "username",
    "contact_name",
    "owner_name",
    "assignee_name",
    "reporter_name",
    "creator_name",
    "first_name",
    "last_name",
    "given_name",
    "family_name",
    "profile_name",
]

# Entity types that typically represent people/identities
IDENTITY_ENTITY_TYPES: Set[str] = {
    # Slack
    "SlackUser",
    "SlackMessage",
    # Gmail
    "GmailMessage",
    "GmailContact",
    "GmailThread",
    # GitHub
    "GitHubUser",
    "GitHubCommit",
    "GitHubIssue",
    "GitHubPullRequest",
    "GitHubComment",
    # Jira
    "JiraUser",
    "JiraIssue",
    "JiraComment",
    # HubSpot
    "HubSpotContact",
    "HubSpotDeal",
    "HubSpotTicket",
    "HubSpotEmail",
    # Notion
    "NotionUser",
    "NotionPage",
    "NotionComment",
    # Linear
    "LinearUser",
    "LinearIssue",
    "LinearComment",
    # Asana
    "AsanaUser",
    "AsanaTask",
    # Google Calendar
    "GoogleCalendarEvent",
    "GoogleCalendarAttendee",
    # Microsoft
    "OutlookMessage",
    "OutlookContact",
    "TeamsMessage",
    "TeamsUser",
    # Salesforce
    "SalesforceContact",
    "SalesforceLead",
    "SalesforceUser",
    # Zendesk
    "ZendeskUser",
    "ZendeskTicket",
    # Intercom
    "IntercomUser",
    "IntercomContact",
    "IntercomConversation",
    # Freshdesk
    "FreshdeskContact",
    "FreshdeskTicket",
    # Generic
    "User",
    "Contact",
    "Person",
    "Member",
    "Author",
}

# Email regex pattern for validation
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
)


@dataclass
class IdentitySignals:
    """Extracted identity signals from an entity."""

    emails: List[str]
    names: List[str]
    primary_email: Optional[str] = None
    primary_name: Optional[str] = None

    @property
    def has_signals(self) -> bool:
        """Check if any signals were extracted."""
        return bool(self.emails or self.names)


def is_valid_email(value: str) -> bool:
    """Check if a string is a valid email address."""
    if not value or not isinstance(value, str):
        return False
    return bool(EMAIL_REGEX.match(value.strip().lower()))


def extract_email_from_string(text: str) -> Optional[str]:
    """Extract an email address from a string that may contain other text.

    For example: "John Doe <john@example.com>" -> "john@example.com"
    """
    if not text or not isinstance(text, str):
        return None

    # Try to find email in angle brackets first (e.g., "Name <email>")
    bracket_match = re.search(r"<([^>]+@[^>]+)>", text)
    if bracket_match:
        email = bracket_match.group(1).strip().lower()
        if is_valid_email(email):
            return email

    # Try to find any email pattern
    email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    if email_match:
        email = email_match.group(0).strip().lower()
        if is_valid_email(email):
            return email

    return None


def extract_name_from_email_string(text: str) -> Optional[str]:
    """Extract a name from a string like "John Doe <john@example.com>".

    Returns the name part before the angle brackets.
    """
    if not text or not isinstance(text, str):
        return None

    # Check for "Name <email>" format
    bracket_match = re.match(r"^([^<]+)<[^>]+>", text)
    if bracket_match:
        name = bracket_match.group(1).strip()
        # Filter out strings that look like emails
        if name and "@" not in name and len(name) > 1:
            return name

    return None


def is_identity_entity_type(entity_type: str) -> bool:
    """Check if an entity type typically represents a person/identity."""
    return entity_type in IDENTITY_ENTITY_TYPES


def extract_identity_signals(
    entity_data: Dict[str, Any],
    entity_type: Optional[str] = None,
) -> IdentitySignals:
    """Extract identity signals (emails, names) from entity data.

    Args:
        entity_data: Dictionary of entity fields and values
        entity_type: Optional entity type name for specialized extraction

    Returns:
        IdentitySignals with extracted emails and names
    """
    emails: List[str] = []
    names: List[str] = []
    seen_emails: Set[str] = set()
    seen_names: Set[str] = set()

    def add_email(email: str) -> None:
        """Add email if valid and not duplicate."""
        email = email.strip().lower()
        if is_valid_email(email) and email not in seen_emails:
            emails.append(email)
            seen_emails.add(email)

    def add_name(name: str) -> None:
        """Add name if valid and not duplicate."""
        name = name.strip()
        # Filter out email-like strings and very short names
        if name and "@" not in name and len(name) > 1 and name.lower() not in seen_names:
            names.append(name)
            seen_names.add(name.lower())

    def process_value(key: str, value: Any) -> None:
        """Process a single field value for identity signals."""
        if value is None:
            return

        key_lower = key.lower()

        # Check if this looks like an email field
        is_email_field = any(
            pattern in key_lower for pattern in EMAIL_FIELD_PATTERNS
        )

        # Check if this looks like a name field
        is_name_field = any(
            pattern in key_lower for pattern in NAME_FIELD_PATTERNS
        )

        if isinstance(value, str):
            if is_email_field or "@" in value:
                # Try to extract email
                email = extract_email_from_string(value)
                if email:
                    add_email(email)
                # Also try to extract name from "Name <email>" format
                name = extract_name_from_email_string(value)
                if name:
                    add_name(name)
            elif is_name_field:
                add_name(value)
            elif is_valid_email(value):
                # Field name doesn't suggest email, but value is a valid email
                add_email(value)

        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    if is_email_field:
                        email = extract_email_from_string(item)
                        if email:
                            add_email(email)
                    elif is_name_field:
                        add_name(item)
                elif isinstance(item, dict):
                    # Recursively process nested dicts
                    nested_signals = extract_identity_signals(item)
                    for email in nested_signals.emails:
                        add_email(email)
                    for name in nested_signals.names:
                        add_name(name)

        elif isinstance(value, dict):
            # Recursively process nested dicts
            nested_signals = extract_identity_signals(value)
            for email in nested_signals.emails:
                add_email(email)
            for name in nested_signals.names:
                add_name(name)

    # Process all fields
    for key, value in entity_data.items():
        process_value(key, value)

    # Determine primary email and name (first found is primary)
    primary_email = emails[0] if emails else None
    primary_name = names[0] if names else None

    return IdentitySignals(
        emails=emails,
        names=names,
        primary_email=primary_email,
        primary_name=primary_name,
    )


def extract_domain_from_email(email: str) -> Optional[str]:
    """Extract domain from an email address."""
    if not email or "@" not in email:
        return None
    return email.split("@")[1].lower()


def normalize_name(name: str) -> str:
    """Normalize a name for comparison.

    - Lowercases
    - Removes extra whitespace
    - Removes common titles/suffixes
    """
    if not name:
        return ""

    # Lowercase and strip
    name = name.lower().strip()

    # Remove common titles
    titles = ["mr.", "mrs.", "ms.", "dr.", "prof.", "sir", "miss"]
    for title in titles:
        if name.startswith(title + " "):
            name = name[len(title) + 1:]

    # Remove common suffixes
    suffixes = ["jr.", "jr", "sr.", "sr", "ii", "iii", "iv", "phd", "md"]
    for suffix in suffixes:
        if name.endswith(" " + suffix):
            name = name[: -(len(suffix) + 1)]

    # Normalize whitespace
    name = " ".join(name.split())

    return name


def calculate_name_similarity(name1: str, name2: str) -> float:
    """Calculate similarity between two names.

    Uses a simple approach:
    - Exact match after normalization = 1.0
    - All words match (different order) = 0.9
    - Partial word match = 0.5-0.8 based on matching ratio
    - No match = 0.0
    """
    norm1 = normalize_name(name1)
    norm2 = normalize_name(name2)

    if not norm1 or not norm2:
        return 0.0

    # Exact match
    if norm1 == norm2:
        return 1.0

    words1 = set(norm1.split())
    words2 = set(norm2.split())

    # All words match (possibly different order)
    if words1 == words2:
        return 0.9

    # Calculate word overlap
    common_words = words1 & words2
    if not common_words:
        return 0.0

    total_words = len(words1 | words2)
    overlap_ratio = len(common_words) / total_words

    # Scale to 0.5-0.8 range based on overlap
    return 0.5 + (overlap_ratio * 0.3)
