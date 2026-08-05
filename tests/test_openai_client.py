"""Tests for src.llm.openai_client — OpenAI wrapper.

These tests mock the OpenAI API so no real calls or credentials are needed.
They verify that:
1. Prompts are loaded from /prompts with the injection guard prepended.
2. The wrapper returns parsed, Pydantic-validated JSON.
3. Errors are raised on invalid responses.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from src.llm.openai_client import PROMPTS_DIR, call_openai, load_prompt


# --- Test response model -------------------------------------------------

class SampleResponse(BaseModel):
    """A simple Pydantic model for testing structured output."""

    title: str
    score: float
    keywords: list[str]


# --- Prompt loading -------------------------------------------------------

class TestLoadPrompt:
    """Tests for the prompt loader."""

    def test_loads_existing_prompt(self) -> None:
        """load_prompt should read and return the prompt file content."""
        prompt = load_prompt("discovery_analyze")

        assert "Discovery and Analysis Agent" in prompt

    def test_prepends_injection_guard(self) -> None:
        """Every loaded prompt must start with the injection guard."""
        prompt = load_prompt("discovery_analyze")

        assert prompt.startswith("IMPORTANT: The user-supplied text below")

    def test_raises_on_missing_prompt(self) -> None:
        """load_prompt should raise FileNotFoundError for non-existent prompts."""
        with pytest.raises(FileNotFoundError, match="not_a_real_prompt"):
            load_prompt("not_a_real_prompt")

    def test_adds_txt_extension(self) -> None:
        """load_prompt should auto-append .txt if not present."""
        prompt_with = load_prompt("discovery_analyze.txt")
        prompt_without = load_prompt("discovery_analyze")

        assert prompt_with == prompt_without


# --- OpenAI call (mocked) ------------------------------------------------

class TestCallOpenAI:
    """Tests for the OpenAI wrapper with mocked API calls."""

    def _mock_response(self, data: dict) -> MagicMock:
        """Build a mock OpenAI ChatCompletion response."""
        choice = MagicMock()
        choice.message.content = json.dumps(data)

        response = MagicMock()
        response.choices = [choice]
        return response

    @patch("src.llm.openai_client.OpenAI")
    def test_returns_parsed_pydantic_model(self, mock_openai_cls: MagicMock) -> None:
        """call_openai should return a validated Pydantic model from the API response."""
        payload = {
            "title": "Senior Backend Engineer",
            "score": 85.5,
            "keywords": ["Python", "FastAPI", "PostgreSQL"],
        }

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_response(payload)
        mock_openai_cls.return_value = mock_client

        result = call_openai(
            prompt_name="discovery_analyze",
            user_message="Some job description text.",
            response_model=SampleResponse,
        )

        assert isinstance(result, SampleResponse)
        assert result.title == "Senior Backend Engineer"
        assert result.score == 85.5
        assert result.keywords == ["Python", "FastAPI", "PostgreSQL"]

    @patch("src.llm.openai_client.OpenAI")
    def test_raises_on_invalid_json(self, mock_openai_cls: MagicMock) -> None:
        """call_openai should raise ValueError if the API returns non-JSON."""
        choice = MagicMock()
        choice.message.content = "This is not JSON"

        response = MagicMock()
        response.choices = [choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response
        mock_openai_cls.return_value = mock_client

        with pytest.raises(ValueError, match="not valid JSON"):
            call_openai(
                prompt_name="discovery_analyze",
                user_message="Some text.",
                response_model=SampleResponse,
            )

    @patch("src.llm.openai_client.OpenAI")
    def test_raises_on_schema_mismatch(self, mock_openai_cls: MagicMock) -> None:
        """call_openai should raise ValueError if JSON doesn't match the model."""
        payload = {"wrong_field": "value"}

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_response(payload)
        mock_openai_cls.return_value = mock_client

        with pytest.raises(ValueError, match="does not match"):
            call_openai(
                prompt_name="discovery_analyze",
                user_message="Some text.",
                response_model=SampleResponse,
            )

    @patch("src.llm.openai_client.OpenAI")
    def test_raises_on_empty_response(self, mock_openai_cls: MagicMock) -> None:
        """call_openai should raise ValueError if API returns None content."""
        choice = MagicMock()
        choice.message.content = None

        response = MagicMock()
        response.choices = [choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response
        mock_openai_cls.return_value = mock_client

        with pytest.raises(ValueError, match="empty response"):
            call_openai(
                prompt_name="discovery_analyze",
                user_message="Some text.",
                response_model=SampleResponse,
            )

    @patch("src.llm.openai_client.OpenAI")
    def test_passes_json_mode(self, mock_openai_cls: MagicMock) -> None:
        """call_openai should request JSON mode from OpenAI."""
        payload = {"title": "Test", "score": 50.0, "keywords": []}

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_response(payload)
        mock_openai_cls.return_value = mock_client

        call_openai(
            prompt_name="discovery_analyze",
            user_message="Text.",
            response_model=SampleResponse,
        )

        call_args = mock_client.chat.completions.create.call_args
        assert call_args.kwargs["response_format"] == {"type": "json_object"}
        assert call_args.kwargs["model"] == "gpt-4.1"
