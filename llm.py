"""Shared OpenRouter transport for the vision and reasoning stages."""

import base64
import json

import requests

import config


class LLMError(RuntimeError):
    """Raised when an OpenRouter request could not be completed."""


def data_url(data, mime_type):
    """Encode raw image bytes as an inline data URL OpenRouter accepts."""
    encoded = base64.b64encode(data).decode("ascii")

    return f"data:{mime_type};base64,{encoded}"


def openrouter_chat(messages, model, max_tokens=None, plugins=None, timeout=None):
    """POST a chat completion to OpenRouter and return the parsed body."""
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens or config.LLM_MAX_TOKENS,
        "temperature": 0.2,
    }

    if plugins:
        payload["plugins"] = plugins

    keys = config.openrouter_api_keys()
    response = None

    for index, api_key in enumerate(keys):
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": config.OPENROUTER_APP_TITLE,
        }

        try:
            response = requests.post(
                config.OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=timeout or config.OPENROUTER_TIMEOUT_SECONDS,
            )

        except requests.exceptions.Timeout as error:
            raise LLMError(f"{model} request timed out") from error

        except requests.exceptions.RequestException as error:
            raise LLMError(f"Could not reach OpenRouter: {error}") from error

        if response.status_code == 200:
            break

        # An expired/revoked key is isolated to that credential. Try the
        # alternate key once before surfacing an authentication error.
        if response.status_code in (401, 403) and index < len(keys) - 1:
            continue

        if response.status_code in (401, 403):
            raise LLMError(
                "OpenRouter authentication failed for all configured keys. "
                "Replace OPENROUTER_API_KEY or OPENROUTER_API_KEY_ALT in .env. "
                f"Last response for {model}: {response.text[:300]}"
            )

        raise LLMError(
            f"OpenRouter returned {response.status_code} for {model}: "
            f"{response.text[:300]}"
        )

    # Responses routinely contain raw control characters inside JSON strings,
    # which trips strict parsing.
    try:
        return json.loads(response.text, strict=False)

    except json.JSONDecodeError as error:
        raise LLMError(f"Unreadable response from OpenRouter: {error}") from error


def extract_message(data):
    """Return (text, finish_reason, annotations) from a completion body."""
    choices = data.get("choices") or []

    if not choices:
        raise LLMError(f"OpenRouter returned no choices: {str(data)[:200]}")

    choice = choices[0]
    message = choice.get("message") or {}

    text = (message.get("content") or "").strip()

    # A reasoning model that exhausted its budget returns null content with the
    # text sitting in the reasoning field instead.
    if not text:
        text = (message.get("reasoning") or "").strip()

    return text, choice.get("finish_reason"), message.get("annotations") or []


def citation_urls(annotations):
    """Pull the real citation URLs out of web-plugin annotations."""
    urls = []

    for annotation in annotations or []:
        url = (annotation.get("url_citation") or {}).get("url")

        if url and url not in urls:
            urls.append(url)

    return urls
