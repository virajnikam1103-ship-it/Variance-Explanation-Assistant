"""Prompt construction, OpenAI calls, and defensive response parsing."""

import json
import os
import re
from typing import Any

from openai import OpenAI


ALLOWED_CLASSIFICATIONS = {"volume", "price", "timing", "anomaly"}
FALLBACK_EXPLANATION = (
    "AI response could not be parsed — please review this driver manually."
)
AI_UNAVAILABLE_MESSAGE = (
    "AI analysis is temporarily unavailable — your data was not lost, please try again"
)


def build_prompt(
    key_drivers: list[dict[str, Any]], analysis_context: str = ""
) -> str:
    """Build the constrained prompt from computed drivers only.

    Takes the Stage 3 driver list and returns a prompt containing only the
    category, percentage, rule tag, and notes the model is allowed to use.
    """
    driver_data = [
        {
            "category": driver["category"],
            "variance_pct": driver["variance_pct"],
            "rule_based_tag": driver["rule_based_tag"],
            "notes": driver["notes"],
        }
        for driver in key_drivers
    ]

    context_block = (
        f"\nAnalyst-provided reasoning or context:\n{analysis_context.strip()}\n"
        if analysis_context.strip()
        else ""
    )

    return f"""
You are explaining forecast-to-actual variance to a finance leadership audience.
Only use the numbers and tags provided below. Do not invent causes, root causes,
or business context not supported by this data. If the data is insufficient to
explain a driver with confidence, say so explicitly in the explanation and lower
the confidence score accordingly.

Return JSON only as an array with one object per key driver. Match each
driver_category exactly to the category name provided. The exact schema is:
[
  {{
    "driver_category": "string, matching the category name given",
    "classification": "one of: volume, price, timing, anomaly",
    "explanation": "one sentence, plain English",
    "confidence": 0
  }}
]

Computed key driver data:
{json.dumps(driver_data, indent=2)}
{context_block}
""".strip()


def _fallback(driver: dict[str, Any]) -> dict[str, Any]:
    """Create a safe fallback result when one driver's AI output is unusable."""
    return {
        "driver_category": driver["category"],
        "classification": "unknown",
        "explanation": FALLBACK_EXPLANATION,
        "confidence": 0,
        "rule_based_tag": driver["rule_based_tag"],
        "low_confidence": True,
        "tag_mismatch": True,
    }


def _strip_code_fences(content: str) -> str:
    """Remove optional markdown fences before JSON parsing."""
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _normalise_result(
    raw_result: Any, driver: dict[str, Any]
) -> dict[str, Any]:
    """Validate one model result and add confidence and mismatch flags."""
    if not isinstance(raw_result, dict):
        return _fallback(driver)

    category = raw_result.get("driver_category")
    classification = raw_result.get("classification")
    explanation = raw_result.get("explanation")
    confidence = raw_result.get("confidence")

    is_valid_confidence = (
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and 0 <= confidence <= 100
    )
    if (
        category != driver["category"]
        or classification not in ALLOWED_CLASSIFICATIONS
        or not isinstance(explanation, str)
        or not explanation.strip()
        or not is_valid_confidence
    ):
        return _fallback(driver)

    confidence_value = float(confidence)
    rule_tag = driver["rule_based_tag"]
    normalized_rule_tag = rule_tag.removesuffix(" driver")
    return {
        "driver_category": driver["category"],
        "classification": classification,
        "explanation": explanation.strip(),
        "confidence": confidence_value,
        "rule_based_tag": rule_tag,
        "low_confidence": confidence_value < 60,
        "tag_mismatch": classification != normalized_rule_tag,
    }


def _parse_response(content: str, key_drivers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse an AI response and provide a safe per-driver fallback."""
    try:
        parsed = json.loads(_strip_code_fences(content))
    except (json.JSONDecodeError, TypeError):
        return [_fallback(driver) for driver in key_drivers]

    # Be tolerant of a model that wraps the requested array while still
    # validating every actual driver against the exact expected schema.
    if isinstance(parsed, dict):
        parsed = parsed.get("drivers") or parsed.get("results") or []
    if not isinstance(parsed, list):
        parsed = []

    by_category = {
        item.get("driver_category"): item
        for item in parsed
        if isinstance(item, dict)
    }
    return [
        _normalise_result(by_category.get(driver["category"]), driver)
        for driver in key_drivers
    ]


def _openai_client() -> OpenAI:
    """Create a fresh OpenAI client using Replit's proxy or the documented key."""
    api_key = os.getenv("AI_INTEGRATIONS_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("AI_INTEGRATIONS_OPENAI_BASE_URL")
    if not api_key:
        raise RuntimeError("OpenAI credentials are not configured")
    client_options: dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_options["base_url"] = base_url
    return OpenAI(**client_options)


def analyze_key_drivers(
    key_drivers: list[dict[str, Any]],
    analysis_context: str = "",
) -> tuple[list[dict[str, Any]], str | None]:
    """Call gpt-4o-mini, parse its JSON, and return flagged driver results.

    Takes Stage 3 key drivers and returns one safely parsed result per driver
    plus an optional user-facing error. A failed call never raises into Flask,
    so the original submission can still be saved.
    """
    if not key_drivers:
        return [], None

    try:
        response = _openai_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": build_prompt(key_drivers, analysis_context),
                }
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "variance_driver_results",
                    "strict": True,
                    "schema": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "driver_category",
                                "classification",
                                "explanation",
                                "confidence",
                            ],
                            "properties": {
                                "driver_category": {"type": "string"},
                                "classification": {
                                    "type": "string",
                                    "enum": ["volume", "price", "timing", "anomaly"],
                                },
                                "explanation": {"type": "string"},
                                "confidence": {"type": "number"},
                            },
                        },
                    },
                },
            },
        )
        content = response.choices[0].message.content or ""
        return _parse_response(content, key_drivers), None
    except Exception as error:  # noqa: BLE001 - API failures must not crash the app.
        print(f"OpenAI analysis failed: {error}")
        return [_fallback(driver) for driver in key_drivers], AI_UNAVAILABLE_MESSAGE