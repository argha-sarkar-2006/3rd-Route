"""Vision stage - read an image with a vision model and describe the problem it poses.

Reads go through OpenRouter's image endpoint using the free model configured in
``config.VISION_OPENROUTER_MODELS``. There is no provider fallback outside that
explicit OpenRouter model list.

Retryable failures are retried with backoff before the next model is tried.
"""

import argparse
import mimetypes
import os
import sys
import time

import config
import llm


DEFAULT_PROMPT = """Transcribe this image and describe the problem it poses.

Write the description once, in a single pass. Output only the description: do
not narrate your thinking, do not repeat yourself, and do not re-check your
work. If something is unclear, give your best reading and continue.

1. One line saying what the image is.
2. Transcribe all visible text faithfully, preserving layout. If the image is a
   word-search grid, write each row of letters as one string on its own line, in
   order from top to bottom, keeping every row the same width as the others.
3. A blank line, then every word or product named in the text around the grid,
   one per line.
4. One sentence stating what a person is expected to do with this."""

# Phrasing this prompt deliberately avoids: "count the letters in each row and
# re-read any row that does not match the others". Small free reasoning models
# read that as licence to re-derive the grid while writing every row, and spend
# the whole output budget in a repetition loop without ever producing the
# description. Stating the deliverable up front and forbidding narration is what
# makes a single clean pass come back.


# Failures worth retrying against the same model before falling through.
RETRYABLE_MARKERS = (
    "503",
    "502",
    "429",
    "unavailable",
    # Providers use both ``RESOURCE_EXHAUSTED`` and camel-case
    # ``ResourceExhausted``; match both so saturation remains retryable.
    "resource_exhausted",
    "resourceexhausted",
    "limit reached",
    "rate-limited",
    "rate limited",
    "connection reset",
    "high demand",
    "overloaded",
    "deadline",
    "timeout",
    "timed out",
)

# Failures that mean this particular model is not usable - skip it, don't retry.
UNAVAILABLE_MODEL_MARKERS = (
    "404",
    "not_found",
    "no longer available",
    "not found",
)


class VisionError(RuntimeError):
    """Raised when no model in the chain could read the image."""


class EmptyResponseError(VisionError):
    """The model answered but produced no text.

    A vision model can spend its whole output budget on hidden reasoning and
    emit no text at all, which leaves the content field empty. That is worth
    retrying or handing to the next model, not a reason to give up.
    """


def _classify(error):
    """Return 'retry', 'next', or 'raise' for a failed request."""
    if isinstance(error, EmptyResponseError):
        return "retry"

    text = f"{type(error).__name__}: {error}".lower()

    # Model-gone markers are checked first on purpose. A retired model answers
    # 404 with prose like "this model is unavailable for free", which also
    # matches the "unavailable" retry marker below - retrying a model that no
    # longer exists just burns the budget before the next one is tried.
    if any(marker in text for marker in UNAVAILABLE_MODEL_MARKERS):
        return "next"

    if any(marker in text for marker in RETRYABLE_MARKERS):
        return "retry"

    return "raise"


def _load_image(image_path):
    if not os.path.exists(image_path):
        raise VisionError(f"Image file does not exist: {image_path}")

    mime_type = mimetypes.guess_type(image_path)[0]

    if not mime_type or not mime_type.startswith("image/"):
        raise VisionError(f"Not a recognised image type: {image_path}")

    with open(image_path, "rb") as handle:
        data = handle.read()

    if not data:
        raise VisionError(f"Image file is empty: {image_path}")

    return data, mime_type


def _read_with_model(model, data, mime_type, prompt):
    """Send the image to a model through OpenRouter and return its text."""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": llm.data_url(data, mime_type)},
                },
            ],
        }
    ]

    body = llm.openrouter_chat(
        messages, model=model, timeout=config.VISION_TIMEOUT_SECONDS
    )

    text, finish, _ = llm.extract_message(body)

    if not text:
        raise EmptyResponseError(f"{model} returned no text (finish_reason={finish})")

    return text


def analyze_image(image_path, prompt=None, log=print):
    """Describe the problem shown in an image.

    Walks the model chain, retrying retryable failures with exponential backoff
    and moving to the next model when one is gone. VISION_TOTAL_BUDGET_SECONDS
    caps the whole stage so a saturated provider cannot retry forever. Raises
    VisionError if no model could read the image.
    """
    data, mime_type = _load_image(image_path)

    prompt = (prompt or "").strip() or DEFAULT_PROMPT

    failures = []
    deadline = time.monotonic() + config.VISION_TOTAL_BUDGET_SECONDS

    for model in config.VISION_OPENROUTER_MODELS:
        if time.monotonic() >= deadline:
            message = (
                f"{model}: skipped, stage budget "
                f"({config.VISION_TOTAL_BUDGET_SECONDS:.0f}s) exhausted"
            )
            failures.append(message)
            log(f"  {message}")
            break

        for attempt in range(1, config.VISION_ATTEMPTS_PER_MODEL + 1):
            if time.monotonic() >= deadline:
                break

            log(
                f"  {model} (attempt "
                f"{attempt}/{config.VISION_ATTEMPTS_PER_MODEL})..."
            )

            try:
                return _read_with_model(model, data, mime_type, prompt)

            except Exception as error:
                verdict = _classify(error)
                failures.append(f"{model}: {error}")

                if verdict == "raise":
                    raise VisionError(f"{model} failed: {error}") from error

                log(f"    {verdict}: {str(error)[:100]}")

                if verdict == "next":
                    break

                if attempt < config.VISION_ATTEMPTS_PER_MODEL:
                    delay = config.VISION_BACKOFF_SECONDS * (2 ** (attempt - 1))

                    if time.monotonic() + delay >= deadline:
                        break

                    log(f"    retrying in {delay:.0f}s...")
                    time.sleep(delay)

        log(f"  {model} unavailable, falling back...")

    raise VisionError(
        "Every model in the fallback chain failed:\n  " + "\n  ".join(failures)
    )


def _print_result(description):
    print()
    print("=" * 60)
    print("VISION RESULT")
    print("=" * 60)
    print()
    print(description)
    print()
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Describe the problem shown in an image."
    )
    parser.add_argument("image", nargs="?", help="Path to the image file")
    parser.add_argument("-p", "--prompt", help="What to analyse in the image")
    args = parser.parse_args()

    print("=" * 60)
    print("       SOVEREIGN AI WORKBENCH")
    print("              VISION")
    print("=" * 60)
    print(f"\nChain: {' -> '.join(config.VISION_OPENROUTER_MODELS)}")

    if args.image:
        print(f"\nAnalysing {args.image}...\n")

        try:
            _print_result(analyze_image(args.image, args.prompt))
            return 0
        except VisionError as error:
            print(f"\nERROR: {error}")
            return 1

    print("\nType 'exit' to stop.")

    while True:
        try:
            image_path = input("\nEnter image path: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nVision model stopped.")
            return 0

        if image_path.lower() in ("exit", "quit"):
            print("Vision model stopped.")
            return 0

        prompt = input(
            "What should the vision model analyze in this image? (Enter for default) "
        ).strip()

        if not image_path:
            print("Please provide an image path.")
            continue

        print("\nAnalyzing image...\n")

        try:
            _print_result(analyze_image(image_path, prompt))
        except VisionError as error:
            print(f"\nERROR: {error}")


if __name__ == "__main__":
    sys.exit(main())
