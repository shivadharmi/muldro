"""Tests for voice service — TTS-friendly output conversion."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.voice_service import VoiceService
from tests.conftest import make_mock_settings


@pytest.fixture
def settings():
    return make_mock_settings()


@pytest.fixture
def mock_db():
    return MagicMock()


@patch("src.services.voice_service.anthropic.AsyncAnthropic")
@pytest.mark.asyncio
async def test_to_voice_success(mock_anthropic_cls, settings, mock_db):
    """Should convert content to voice-friendly format via Claude."""
    mock_client = MagicMock()
    response_data = {
        "spoken_text": "Good morning. You have three priorities today.",
        "duration_hint": "short",
    }
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(response_data))]
    mock_client.messages.create = AsyncMock(return_value=response)
    mock_anthropic_cls.return_value = mock_client

    service = VoiceService(settings=settings, db=mock_db)
    result = await service.to_voice("# Daily Briefing\n- Task 1\n- Task 2", "briefing")

    assert result["spoken_text"] == "Good morning. You have three priorities today."
    assert result["duration_hint"] == "short"


@patch("src.services.voice_service.anthropic.AsyncAnthropic")
@pytest.mark.asyncio
async def test_to_voice_fallback(mock_anthropic_cls, settings, mock_db):
    """Should fall back to stripped text when Claude fails."""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API error"))
    mock_anthropic_cls.return_value = mock_client

    service = VoiceService(settings=settings, db=mock_db)
    result = await service.to_voice("## Heading\n**Bold text** and *italic*", "general")

    assert "Heading" in result["spoken_text"]
    assert "Bold text" in result["spoken_text"]
    assert "**" not in result["spoken_text"]
    assert "##" not in result["spoken_text"]


def test_strip_to_voice(settings, mock_db):
    """Should strip markdown formatting for voice readability."""
    with patch("src.services.voice_service.anthropic.AsyncAnthropic"):
        service = VoiceService(settings=settings, db=mock_db)

    content = (
        "## Priority Items\n"
        "- **Meeting** with Alice at 2:30\n"
        "- Review [deck](https://example.com)\n"
        "- Check `deploy status`\n"
    )

    result = service._strip_to_voice(content)

    assert "##" not in result
    assert "**" not in result
    assert "- " not in result.split("\n")[0]
    assert "deck" in result
    assert "https://example.com" not in result
    assert "`" not in result
