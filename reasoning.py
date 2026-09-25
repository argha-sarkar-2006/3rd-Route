"""Reasoning stage - research a problem, summarise it, and classify the ask.

Given a user prompt (optionally backed by a vision description) this stage:
  1. retrieves relevant entries from the local knowledge base,
  2. optionally consults the web through a local search adapter,
  3. produces an end-to-end summary,
  4. decides whether the user wants an answer or working code.

Retrieval runs locally and the hits are injected into the prompt. Web results
are also injected with their source URLs.
"""

import argparse
import json
import re
import sys

import config
import knowledge
import llm
import websearch


class ReasoningError(llm.LLMError):
    """Raised when the reasoning stage could not produce a result."""


SYSTEM_PROMPT = """You are the reasoning stage of a private AI workbench.

Research the user's problem and return a single JSON object describing it. You
have web search available - use it whenever the answer depends on current
facts, versions, prices, or anything you are not certain of from memory.

Return ONLY this JSON, with no prose around it:

{
  "intent": "query" or "code",
  "summary": "an end-to-end summary of the problem and your findings",
  "problem_statement": "a self-contained specification of what must be built",
  "language": "the programming language required",
  "sources": ["https://..."]
}

Rules:
- "intent" is "code" only when the user asks for code, a script, a program, an
  implementation, or something to be built. Asking for an explanation, a fact,
  a comparison or general advice is "query".
- "summary" must stand alone. Explain the problem and the findings, and when
  intent is "query" give the answer itself.
- "problem_statement" must be complete enough that a separate coding model
  which has never seen this conversation can implement it without asking
  questions: inputs, outputs, constraints and edge cases. Use "" when intent
  is "query".
- Never invent a URL. Cite only pages that were actually retrieved.
- If the local knowledge base contradicts the web, say so in the summary."""


# Marks a request as "build me this" rather than "tell me about this". Used only
# when the model's structured answer is unusable and we have to decide intent
# without it.
CODE_REQUEST_MARKERS = (
    "write a",
    "write me",
    "script",
    "program",
    "code",
    "implement",
    "function",
    "algorithm",
    "solver",
    "solve",
    "build a",
    "create a",
    "develop a",
)


def _guess_intent(prompt):
    """Decide intent from the prompt alone, when the model could not.

    The model occasionally leaks its own reasoning instead of the JSON we ask
    for, in which case there is no intent to read. Defaulting that case to
    "query" silently drops the code path for a user who asked for code, which
    is the worse of the two mistakes: a needless coding call wastes a request,
    a missed one fails the request outright.
    """
    text = (prompt or "").lower()

    return "code" if any(marker in text for marker in CODE_REQUEST_MARKERS) else "query"


# ============================================================
# OLLAMA
# ============================================================

def _call_model(messages, use_web=True, max_tokens=None):
    # The transport raises the base LLMError, which the callers above do not
    # catch - only ReasoningError. Converting here means a timeout surfaces as a
    # clean message instead of a traceback from the router.
    try:
        return llm.ollama_chat(
            messages,
            model=config.REASONING_MODEL,
            max_tokens=max_tokens,
        )

    except llm.LLMError as error:
        raise ReasoningError(f"the reasoning model could not be reached: {error}") from error


# ============================================================
# PARSING
# ============================================================

def _parse_json(text):
    """Pull a JSON object out of a model response, tolerating fences and prose."""
    text = (text or "").strip()

    if not text:
        return None

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)

    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text, strict=False)

    except json.JSONDecodeError:
        pass

    start, end = text.find("{"), text.rfind("}")

    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1], strict=False)

        except json.JSONDecodeError:
            return None

    return None


def _normalise(parsed, annotations, raw):
    intent = str(parsed.get("intent") or "").strip().lower()

    declared = parsed.get("sources") or []

    if not isinstance(declared, list):
        declared = [declared]

    # Prefer the plugin's real citations over anything the model typed.
    sources = llm.citation_urls(annotations) or [
        str(source) for source in declared if str(source).startswith("http")
    ]

    return {
        "intent": "code" if intent == "code" else "query",
        "summary": str(parsed.get("summary") or raw or "").strip(),
        "problem_statement": str(parsed.get("problem_statement") or "").strip(),
        "language": str(parsed.get("language") or "").strip(),
        "sources": sources,
    }


# ============================================================
# PROMPT ASSEMBLY
# ============================================================

def _build_user_message(
    prompt, image_description=None, knowledge_context="", web_context=""
):
    sections = []

    if image_description:
        sections.append(
            "DESCRIPTION OF THE ATTACHED IMAGE\n"
            "Produced by the vision stage. Treat it as an accurate reading of "
            "the image.\n\n"
            f"{image_description}"
        )

    if knowledge_context:
        sections.append(
            "LOCAL KNOWLEDGE BASE EXCERPTS\n"
            "Organization-specific material. Prefer it for anything it covers.\n\n"
            f"{knowledge_context}"
        )

    if web_context:
        sections.append(
            "WEB SEARCH RESULTS\n"
            "Use these retrieved results for current facts and cite their URLs.\n\n"
            f"{web_context}"
        )

    sections.append(f"USER REQUEST\n{prompt}")

    return "\n\n".join(sections)


# ============================================================
# ENTRY POINT
# ============================================================

def reason(
    prompt,
    image_description=None,
    use_web=True,
    use_knowledge=True,
    db_path=None,
    log=print,
):
    """Research a prompt and return a structured result.

    Returns a dict with keys: intent, summary, problem_statement, language,
    sources.
    """
    prompt = (prompt or "").strip()

    if not prompt:
        raise ReasoningError("A prompt is required.")

    knowledge_context = ""
    web_results = websearch.search(prompt, limit=5, log=log) if use_web else []

    if use_knowledge:
        query = " ".join(part for part in (prompt, image_description) if part)
        knowledge_context = knowledge.context_for(query, limit=5, db_path=db_path)

        log(
            f"  knowledge base: {len(knowledge_context)} characters retrieved"
            if knowledge_context
            else "  knowledge base: no relevant entries"
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _build_user_message(
                prompt,
                image_description,
                knowledge_context,
                websearch.format_results(web_results),
            ),
        },
    ]

    log(
        f"  consulting {config.REASONING_MODEL}"
        f"{' with web search' if use_web else ''}..."
    )

    data = _call_model(messages, use_web=use_web)
    content, finish, annotations = llm.extract_message(data)
    parsed = _parse_json(content)

    # A response cut off at the token limit means the JSON is incomplete.
    if parsed is None and finish == "length":
        log("  response was truncated, retrying with a larger budget...")

        data = _call_model(messages, use_web=use_web, max_tokens=config.LLM_MAX_TOKENS * 2)
        content, finish, annotations = llm.extract_message(data)
        parsed = _parse_json(content)

    if parsed is None:
        # Keep the prose rather than losing the answer entirely, and recover the
        # intent from the prompt since the model never stated it.
        log("  could not parse structured output, returning prose instead")

        return {
            "intent": _guess_intent(prompt),
            "summary": content,
            "problem_statement": "",
            "language": "",
            "sources": [item["url"] for item in web_results]
            + llm.citation_urls(annotations),
        }

    result = _normalise(parsed, annotations, content)
    result["sources"] = list(
        dict.fromkeys([item["url"] for item in web_results] + result["sources"])
    )
    return result


def _print_result(result):
    print()
    print("=" * 60)
    print(f"INTENT: {result['intent'].upper()}")
    print("=" * 60)

    print("\nSUMMARY\n")
    print(result["summary"] or "(empty)")

    if result["problem_statement"]:
        print("\nPROBLEM STATEMENT\n")
        print(result["problem_statement"])

    if result["language"]:
        print(f"\nLANGUAGE: {result['language']}")

    if result["sources"]:
        print("\nSOURCES")
        for source in result["sources"]:
            print(f"  - {source}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Research a problem and classify whether it needs code."
    )
    parser.add_argument("prompt", nargs="?", help="The problem to research")
    parser.add_argument("--image-description", help="Output of the vision stage")
    parser.add_argument("--no-web", action="store_true", help="Disable web search")
    parser.add_argument("--no-knowledge", action="store_true", help="Skip the knowledge base")
    args = parser.parse_args()

    print("=" * 60)
    print("        SOVEREIGN AI - REASONING")
    print("=" * 60)
    print(f"Model: {config.REASONING_MODEL}")
    print(f"Knowledge base: {knowledge.document_count()} document(s)")

    if args.prompt:
        try:
            _print_result(
                reason(
                    args.prompt,
                    image_description=args.image_description,
                    use_web=not args.no_web,
                    use_knowledge=not args.no_knowledge,
                )
            )
            return 0

        except ReasoningError as error:
            print(f"\nERROR: {error}")
            return 1

    print("\nType 'exit' to stop.\n")

    while True:
        try:
            prompt = input("You: ").strip()

        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            return 0

        if prompt.lower() in ("exit", "quit"):
            print("Goodbye.")
            return 0

        if not prompt:
            continue

        try:
            _print_result(
                reason(prompt, use_web=not args.no_web, use_knowledge=not args.no_knowledge)
            )

        except ReasoningError as error:
            print(f"\nERROR: {error}")


if __name__ == "__main__":
    sys.exit(main())
