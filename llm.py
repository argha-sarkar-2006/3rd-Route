"""Shared local Ollama transport for every model stage."""

import base64

from ollama import Client

import config


class LLMError(RuntimeError):
    """Raised when a local Ollama request cannot be completed."""


def data_url(data, mime_type):
    """Encode raw bytes as a data URL for compatibility with older callers."""
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _client():
    headers = {}
    api_key = config.ollama_api_key()

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return Client(host=config.OLLAMA_HOST, headers=headers)


def ollama_chat(messages, model, max_tokens=None, timeout=None):
    """Call a local Ollama model and return an OpenAI-shaped response dict."""
    options = {}

    if max_tokens:
        options["num_predict"] = max_tokens

    try:
        response = _client().chat(
            model=model,
            messages=messages,
            options=options or None,
        )
    except Exception as error:
        raise LLMError(f"Ollama request failed for {model}: {error}") from error

    message = getattr(response, "message", None)
    if message is None:
        raise LLMError(f"Ollama returned no message for {model}")

    content = (getattr(message, "content", None) or "").strip()
    reasoning = (getattr(message, "thinking", None) or "").strip()

    return {
        "choices": [
            {
                "message": {"content": content, "reasoning": reasoning},
                "finish_reason": getattr(response, "done_reason", None),
            }
        ]
    }


def extract_message(data):
    """Return (text, finish_reason, annotations) from an Ollama response."""
    choices = data.get("choices") or []

    if not choices:
        raise LLMError("Ollama returned no choices")

    choice = choices[0]
    message = choice.get("message") or {}
    text = (message.get("content") or "").strip()

    if not text:
        text = (message.get("reasoning") or "").strip()

    return text, choice.get("finish_reason"), []


def citation_urls(annotations):
    """Return URLs from normalized web-search annotations."""
    urls = []

    for annotation in annotations or []:
        url = annotation.get("url") if isinstance(annotation, dict) else None

        if url and url not in urls:
            urls.append(url)

    return urls
