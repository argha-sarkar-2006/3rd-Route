"""Coding stage - turn a reasoning result into working code.

Runs on the local Ollama daemon. The interactive chat from the original version is kept,
and generate_code() is the entry point the router uses.
"""

import sys

from ollama import Client

import config


SYSTEM_PROMPT = """You are the coding stage of a private AI workbench.

You are given a specification written by the reasoning stage, which has
already done the research. Turn it into working code.

Rules:
- Put the filename on its own line before the code block, in the form:
  FILE: solution.py
- Then give one fenced code block containing the complete program.
- Never leave placeholders such as "# rest of the code here" or "...".
  Every function you mention must be fully implemented.
- Include all imports and a runnable entry point.
- Handle the edge cases named in the specification.
- Any literal data you hardcode from a transcription - a letter grid, a table, a
  block of CSV - must be normalised at startup: if its rows differ in length,
  pad or trim them to a common width and report what you changed. Transcribed
  input is routinely ragged, so never validate it strictly and abort: the
  program must still run and solve.
- Do not invent APIs or libraries that do not exist.
- After the code, briefly explain the design decisions."""


class CodingError(RuntimeError):
    """Raised when the coding stage could not produce code."""


def _client():
    headers = {}
    api_key = config.ollama_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return Client(host=config.OLLAMA_HOST, headers=headers)


def _build_prompt(problem_statement, language, summary):
    parts = [f"SPECIFICATION\n{problem_statement}"]

    if language:
        parts.append(f"REQUIRED LANGUAGE\n{language}")

    if summary:
        parts.append(f"CONTEXT FROM RESEARCH\n{summary}")

    parts.append(
        "Implement the specification. Include a way to run it - a command-line "
        "entry point or a usage example."
    )

    return "\n\n".join(parts)


def generate_code(problem_statement, language="", summary="", model=None, log=print):
    """Generate code from a specification produced by the reasoning stage."""
    problem_statement = (problem_statement or "").strip()

    if not problem_statement:
        raise CodingError("A problem statement is required to generate code.")

    model = model or config.CODING_MODEL
    log(f"  asking {model} for code...")

    try:
        response = _client().chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_prompt(problem_statement, language, summary),
                },
            ],
        )

    except Exception as error:
        raise CodingError(f"{model} failed: {error}") from error

    code = (response.message.content or "").strip()

    if not code:
        raise CodingError(f"{model} returned no code.")

    return code


# ============================================================
# STANDALONE CHAT
# ============================================================

def chat(user_input, messages=None):
    messages = messages if messages is not None else [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    messages.append({"role": "user", "content": user_input})

    try:
        response = _client().chat(model=config.CODING_MODEL, messages=messages)
        answer = response.message.content

    except Exception as error:
        messages.pop()
        return f"Error: {error}"

    messages.append({"role": "assistant", "content": answer})

    return answer


def main():
    print("=" * 60)
    print("                 LOCAL OLLAMA CHATBOT")
    print("=" * 60)
    print(f"\nModel: {config.CODING_MODEL}")
    print("Type 'exit' to stop. Type 'clear' to clear the conversation.\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("You: ")

        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            return 0

        if user_input.lower() == "exit":
            print("Goodbye!")
            return 0

        if user_input.lower() == "clear":
            del messages[1:]
            print("Conversation cleared.\n")
            continue

        if not user_input.strip():
            continue

        print(f"\nAI: {chat(user_input, messages)}\n")


if __name__ == "__main__":
    sys.exit(main())
