"""Router and orchestrator - the workbench front door.

Decides which stage handles a request, then runs the pipeline:

    image + prompt -> vision -> reasoning -> coding (only when code is wanted)
    prompt only    -> routing  -> reasoning -> coding (only when code is wanted)

An attached image is unambiguous, so it goes straight to the vision stage
without spending a router call. The Needle router is used for text-only
requests; if it selects "coding" the coding stage runs regardless of how the
reasoning stage classified the request, which makes the router's verdict an
explicit user-intent override rather than decoration.
"""

import os 

# Must be set before the Needle agent is constructed.
os.environ.setdefault("NEEDLE_TELEMETRY", "0")

import argparse
import sys
from typing import Literal

import needle

import coding
import config
import knowledge
import reasoning
import vision


DEFAULT_IMAGE_PROMPT = (
    "Read the problem shown in this image and work out what solving it requires."
)


# ============================================================
# NEEDLE ROUTER
# ============================================================

@needle.tool
def select_model(task_type: Literal["coding", "reasoning", "vision"]):
    """
    Select the correct AI model for the user's task.

    Args:
        task_type:
            coding = programming, software development, debugging,
                     APIs, algorithms, code generation

            reasoning = mathematics, logical reasoning, technical
                       analysis, planning, calculations, problem solving

            vision = images, photographs, scanned documents,
                     engineering drawings, charts, diagrams,
                     visual inspection
    """
    return {"route": task_type}


ROUTER_SYSTEM_PROMPT = (
    "You are a request router. For every request you must call the select_model "
    "tool exactly once with the best matching task_type. Never answer the request "
    "yourself."
)


_agent = None


def _get_agent():
    global _agent

    if _agent is None:
        _agent = needle.Needle(tools=[select_model], system=ROUTER_SYSTEM_PROMPT)

    return _agent


def route_text(prompt, log=print):
    """Ask the Needle router which stage a text-only prompt belongs to.

    Needle is a small local model. It calls select_model reliably once the
    agent has a system instruction, but its coding-vs-reasoning call is not
    sharp, so a "reasoning" verdict is not treated as final: the reasoning
    stage classifies the request again and forwards to coding when the request
    actually asks for code.
    """
    try:
        response = _get_agent().complete(prompt)

    except Exception as error:
        log(f"[router] unavailable ({error}), defaulting to reasoning")
        return "reasoning"

    for call in response.get("function_calls") or []:
        route = (call.get("arguments") or {}).get("task_type")

        if route in ("coding", "reasoning", "vision"):
            return route

    return "reasoning"


# ============================================================
# PIPELINE
# ============================================================

def run(image_path=None, prompt="", use_web=True, log=print):
    """Run the pipeline and return a dict describing what happened.

    Keys: route, intent, summary, problem_statement, language, sources,
    image_description, and code when the coding stage ran.
    """
    prompt = (prompt or "").strip()
    image_description = None

    if image_path:
        route = "vision"
        log(f"[vision] reading {image_path}")

        image_description = vision.analyze_image(
            image_path, prompt or DEFAULT_IMAGE_PROMPT, log=log
        )

        log("[vision] description ready")

    else:
        if not prompt:
            raise ValueError("Provide an image path or a prompt.")

        route = route_text(prompt, log=log)
        log(f"[router] needle selected: {route}")

        if route == "vision":
            log("[router] no image supplied, falling back to reasoning")
            route = "reasoning"

    force_coding = route == "coding"

    log("[reasoning] researching...")

    result = reasoning.reason(
        prompt or DEFAULT_IMAGE_PROMPT,
        image_description=image_description,
        use_web=use_web,
        log=log,
    )

    result["route"] = route
    result["image_description"] = image_description

    if result["intent"] == "code" or force_coding:
        if force_coding and result["intent"] != "code":
            log("[router] overriding intent: the router selected coding")

        log("[coding] generating code...")

        # A prose fallback from the reasoning stage has no specification, and its
        # text is the model's own reasoning rather than research. Handing that to
        # the coding model makes it continue the monologue instead of writing
        # code, so drop it and let the original request stand as the spec.
        context = result["summary"] if result["problem_statement"] else ""

        result["code"] = coding.generate_code(
            result["problem_statement"] or prompt,
            language=result["language"],
            summary=context,
            log=log,
        )

    return result


# ============================================================
# OUTPUT
# ============================================================

def print_report(result):
    print()
    print("=" * 60)
    print("                    RESULT")
    print("=" * 60)
    print(f"\nROUTE:  {result['route']}")
    print(f"INTENT: {result['intent'].upper()}")

    if result["summary"]:
        print("\nSUMMARY\n")
        print(result["summary"])

    if result["sources"]:
        print("\nSOURCES")
        for source in result["sources"]:
            print(f"  - {source}")

    if result.get("code"):
        print("\n" + "=" * 60)
        print("CODE")
        print("=" * 60)
        print()
        print(result["code"])

    print()


# ============================================================
# CLI
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Route a request to the right model and run the pipeline."
    )
    parser.add_argument("--image", help="Path to an image to analyse")
    parser.add_argument("--text", help="A text-only request")
    parser.add_argument("--prompt", help="What to do with the image")
    parser.add_argument("--no-web", action="store_true", help="Disable web search")
    return parser.parse_args()


def banner():
    print("=" * 60)
    print("            SOVEREIGN AI WORKBENCH")
    print("               NEEDLE ROUTER")
    print("=" * 60)
    print(f"Vision:    {' -> '.join(config.VISION_OPENROUTER_MODELS)}")
    print(f"Reasoning: {config.REASONING_MODEL}")
    print(f"Coding:    {config.CODING_MODEL}")
    print(f"Knowledge: {knowledge.document_count()} document(s)")


def interactive(use_web=True):
    print("\nType 'exit' to stop.\n")

    while True:
        try:
            image_path = input("Image path (Enter to skip): ").strip()

        except (KeyboardInterrupt, EOFError):
            print("\nRouter stopped.")
            return 0

        if image_path.lower() in ("exit", "quit"):
            print("Router stopped.")
            return 0

        if image_path and not os.path.exists(image_path):
            print(f"  no such file: {image_path}\n")
            continue

        try:
            prompt = input("Your request: ").strip()

        except (KeyboardInterrupt, EOFError):
            print("\nRouter stopped.")
            return 0

        if not image_path and not prompt:
            print("  Provide an image path or a request.\n")
            continue

        try:
            print_report(
                run(
                    image_path=image_path or None,
                    prompt=prompt,
                    use_web=use_web,
                )
            )

        except (ValueError, vision.VisionError, reasoning.ReasoningError, coding.CodingError) as error:
            print(f"\nERROR: {error}\n")


def main():
    args = parse_args()
    banner()

    use_web = not args.no_web

    if args.image or args.text:
        try:
            result = run(
                image_path=args.image,
                prompt=args.prompt or args.text or "",
                use_web=use_web,
            )

        except (ValueError, vision.VisionError, reasoning.ReasoningError, coding.CodingError) as error:
            print(f"\nERROR: {error}")
            return 1

        print_report(result)
        return 0

    return interactive(use_web=use_web)


if __name__ == "__main__":
    sys.exit(main())
