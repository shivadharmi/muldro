"""Google Calendar actions served through the OpenConnector gateway.

Action ids, parameter names, and input schemas are transcribed verbatim from a
live OpenConnector v1.3.5 catalog -- see infra/gateway/spike-findings-multiprovider.md.
OC's runtime ``get_action_guide`` exposes no machine-readable schema, so these
are declared here; warm_start's live drift check warns when OC's parameter names
diverge from what is declared.
"""

from __future__ import annotations

import copy

from src.integrations.gateway_actions._types import GatewayAction, GatewayProvider

# ``create_event`` and ``update_event`` take the SAME nested ``event`` payload
# upstream -- OpenConnector declares both from one shared shape, and they differ
# only in the wrapper's description and required list. Declaring the 16 shared
# property schemas once means a future OC drift correction is a one-place edit
# instead of two blocks that can silently disagree.
_EVENT_PROPERTIES: dict = {
    "summary": {
        "type": "string",
        "description": "Event title.",
    },
    "description": {
        "type": "string",
        "description": "Event description.",
    },
    "location": {
        "type": "string",
        "description": "Event location.",
    },
    "start": {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "minLength": 1,
                "description": "All-day event date in YYYY-MM-DD format.",
            },
            "dateTime": {
                "type": "string",
                "description": "RFC 3339 timestamp.",
                "format": "date-time",
            },
            "timeZone": {
                "type": "string",
                "minLength": 1,
                "description": "IANA time zone used to interpret the event time.",
            },
        },
        "additionalProperties": False,
        "description": "Event date or date-time.",
    },
    "end": {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "minLength": 1,
                "description": "All-day event date in YYYY-MM-DD format.",
            },
            "dateTime": {
                "type": "string",
                "description": "RFC 3339 timestamp.",
                "format": "date-time",
            },
            "timeZone": {
                "type": "string",
                "minLength": 1,
                "description": "IANA time zone used to interpret the event time.",
            },
        },
        "additionalProperties": False,
        "description": "Event date or date-time.",
    },
    "attendees": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Attendee email address.",
                },
                "displayName": {
                    "type": "string",
                    "description": "Attendee display name.",
                },
                "optional": {
                    "type": "boolean",
                    "description": "Whether attendance is optional.",
                },
                "resource": {
                    "type": "boolean",
                    "description": "Whether the attendee represents a resource.",
                },
                "responseStatus": {
                    "type": "string",
                    "description": "Attendee response status.",
                },
                "comment": {
                    "type": "string",
                    "description": "Additional attendee comment.",
                },
                "additionalGuests": {
                    "type": "integer",
                    "description": "Number of additional guests.",
                },
            },
            "additionalProperties": False,
            "required": [
                "email",
            ],
            "description": "Event attendee.",
        },
        "description": "Event attendees.",
    },
    "recurrence": {
        "type": "array",
        "items": {
            "type": "string",
            "minLength": 1,
        },
        "description": "Recurrence rules.",
    },
    "conferenceData": {
        "type": "object",
        "additionalProperties": True,
        "description": "Google Calendar API object.",
    },
    "reminders": {
        "type": "object",
        "properties": {
            "useDefault": {
                "type": "boolean",
                "description": "Whether to use default calendar reminders.",
            },
            "overrides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Reminder delivery method, such as email or popup.",
                        },
                        "minutes": {
                            "type": "integer",
                            "description": "Minutes before the event.",
                        },
                    },
                    "additionalProperties": False,
                    "required": [
                        "method",
                        "minutes",
                    ],
                    "description": "Reminder override.",
                },
                "description": "Reminder overrides.",
            },
        },
        "additionalProperties": False,
        "description": "Event reminders.",
    },
    "colorId": {
        "type": "string",
        "description": "Google Calendar color ID.",
    },
    "visibility": {
        "type": "string",
        "description": "Event visibility.",
    },
    "transparency": {
        "type": "string",
        "description": "Whether the event blocks time.",
    },
    "status": {
        "type": "string",
        "description": "Event status.",
    },
    "extendedProperties": {
        "type": "object",
        "additionalProperties": True,
        "description": "Google Calendar API object.",
    },
    "attachments": {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": True,
            "description": "Google Calendar API object.",
        },
        "description": "Google Calendar API objects.",
    },
    "source": {
        "type": "object",
        "additionalProperties": True,
        "description": "Google Calendar API object.",
    },
}

GOOGLECALENDAR_ACTIONS: tuple[GatewayAction, ...] = (
    GatewayAction(
        "googlecalendar.list_calendars",
        "calendar.list",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "maxResults": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 250,
                    "description": "Maximum calendar list entries to return.",
                },
                "pageToken": {
                    "type": "string",
                    "description": "Page token.",
                },
                "syncToken": {
                    "type": "string",
                    "description": "Incremental sync token.",
                },
                "showHidden": {
                    "type": "boolean",
                    "description": "Include hidden calendars.",
                },
                "showDeleted": {
                    "type": "boolean",
                    "description": "Include deleted calendars.",
                },
                "minAccessRole": {
                    "type": "string",
                    "description": "Minimum access role.",
                },
            },
            "additionalProperties": False,
            "description": "The input payload for this action.",
        },
    ),
    GatewayAction(
        "googlecalendar.list_events",
        "calendar.list",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "calendarId": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Google Calendar ID. Omit to use the primary calendar when "
                    "supported.",
                },
                "q": {
                    "type": "string",
                    "description": "Full-text event search query.",
                },
                "iCalUID": {
                    "type": "string",
                    "description": "iCalendar UID filter.",
                },
                "orderBy": {
                    "type": "string",
                    "description": "Sort order.",
                },
                "timeMin": {
                    "type": "string",
                    "description": "RFC 3339 timestamp.",
                    "format": "date-time",
                },
                "timeMax": {
                    "type": "string",
                    "description": "RFC 3339 timestamp.",
                    "format": "date-time",
                },
                "timeZone": {
                    "type": "string",
                    "description": "Response time zone.",
                },
                "pageToken": {
                    "type": "string",
                    "description": "Page token.",
                },
                "syncToken": {
                    "type": "string",
                    "description": "Incremental sync token.",
                },
                "eventTypes": {
                    "anyOf": [
                        {
                            "type": "string",
                            "minLength": 1,
                        },
                        {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "minItems": 1,
                        },
                    ],
                    "description": "One string or an array of strings.",
                },
                "maxResults": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2500,
                    "description": "Maximum events to return.",
                },
                "updatedMin": {
                    "type": "string",
                    "description": "RFC 3339 timestamp.",
                    "format": "date-time",
                },
                "showDeleted": {
                    "type": "boolean",
                    "description": "Include deleted events.",
                },
                "maxAttendees": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum attendees per event.",
                },
                "singleEvents": {
                    "type": "boolean",
                    "description": "Expand recurring events.",
                },
                "showHiddenInvitations": {
                    "type": "boolean",
                    "description": "Include hidden invitations.",
                },
                "sharedExtendedProperty": {
                    "anyOf": [
                        {
                            "type": "string",
                            "minLength": 1,
                        },
                        {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "minItems": 1,
                        },
                    ],
                    "description": "One string or an array of strings.",
                },
                "privateExtendedProperty": {
                    "anyOf": [
                        {
                            "type": "string",
                            "minLength": 1,
                        },
                        {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "minItems": 1,
                        },
                    ],
                    "description": "One string or an array of strings.",
                },
            },
            "additionalProperties": False,
            "required": [
                "calendarId",
            ],
            "description": "The input payload for this action.",
        },
    ),
    GatewayAction(
        "googlecalendar.get_event",
        "calendar.get",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "calendarId": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Google Calendar ID. Omit to use the primary calendar when "
                    "supported.",
                },
                "eventId": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Google Calendar event ID.",
                },
            },
            "additionalProperties": False,
            "required": [
                "calendarId",
                "eventId",
            ],
            "description": "The input payload for this action.",
        },
    ),
    GatewayAction(
        "googlecalendar.free_busy_query",
        "calendar.get",
        "low",
        False,
        {
            "type": "object",
            "properties": {
                "items": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "minItems": 1,
                        },
                        {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {
                                        "type": "string",
                                        "minLength": 1,
                                    },
                                },
                                "additionalProperties": False,
                                "required": [
                                    "id",
                                ],
                            },
                            "minItems": 1,
                        },
                    ],
                    "description": "Calendar or group IDs to include in the freeBusy query.",
                },
                "timeMin": {
                    "type": "string",
                    "description": "RFC 3339 timestamp.",
                    "format": "date-time",
                },
                "timeMax": {
                    "type": "string",
                    "description": "RFC 3339 timestamp.",
                    "format": "date-time",
                },
                "timeZone": {
                    "type": "string",
                    "description": "Response time zone.",
                },
                "groupExpansionMax": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum calendars to expand per group.",
                },
                "calendarExpansionMax": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum calendars to return after expansion.",
                },
            },
            "additionalProperties": False,
            "required": [
                "items",
                "timeMin",
                "timeMax",
            ],
            "description": "The input payload for this action.",
        },
    ),
    GatewayAction(
        "googlecalendar.create_event",
        "calendar.create",
        "medium",
        True,
        {
            "type": "object",
            "properties": {
                "calendarId": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Google Calendar ID. Omit to use the primary calendar when "
                    "supported.",
                },
                "event": {
                    "type": "object",
                    "properties": copy.deepcopy(_EVENT_PROPERTIES),
                    "additionalProperties": False,
                    "required": [
                        "start",
                        "end",
                    ],
                    "description": "Event creation payload.",
                },
            },
            "additionalProperties": False,
            "required": [
                "calendarId",
                "event",
            ],
            "description": "The input payload for this action.",
        },
    ),
    GatewayAction(
        "googlecalendar.update_event",
        "calendar.update",
        "medium",
        True,
        {
            "type": "object",
            "properties": {
                "calendarId": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Google Calendar ID. Omit to use the primary calendar when "
                    "supported.",
                },
                "eventId": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Google Calendar event ID.",
                },
                "event": {
                    "type": "object",
                    "properties": copy.deepcopy(_EVENT_PROPERTIES),
                    "additionalProperties": False,
                    "description": "Writable Google Calendar event fields.",
                },
            },
            "additionalProperties": False,
            "required": [
                "calendarId",
                "eventId",
                "event",
            ],
            "description": "The input payload for this action.",
        },
    ),
)

GOOGLECALENDAR = GatewayProvider(
    provider_id="googlecalendar",
    server_name="google-workspace",
    actions=GOOGLECALENDAR_ACTIONS,
)
