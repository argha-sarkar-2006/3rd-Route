import os
import sys

import config
import llm
import knowledge
import modelrouter
from knowledge import chunk_text, extract_pdf_text


MODEL = config.REASONING_MODEL


SYSTEM_PROMPT = """
You are the AI agent inside a private enterprise AI workbench.

ROLE:
You are a senior software engineer, technical analyst and
enterprise knowledge assistant.

CORE BEHAVIOR: 
1. Analyze the user's requirement before answering.
2. Break complex problems into logical steps.
3. Produce accurate, practical and production-quality solutions.
4. Never invent APIs, libraries, company policies or facts.
5. Clearly identify assumptions.
6. If information is missing, say so instead of hallucinating.
7. Prefer secure and maintainable engineering practices.
8. When company documentation is provided, treat it as the
   primary source of truth.
9. Do not contradict company documentation without explaining
   the conflict.
10. Treat all uploaded organizational information as confidential.

DOCUMENT HANDLING:
- Use the supplied organization knowledge when answering.
- Distinguish between information found in the documents and
  your general knowledge.
- Do not claim that information exists in the documents if it
  does not.
- If the documents do not contain enough information, explicitly
  state that additional information is required.

SOFTWARE ENGINEERING:
- Write clean and maintainable code.
- Explain important architectural decisions.
- Check for syntax and logical errors.
- Mention dependencies when required.
- Prefer secure implementations.
- Do not blindly follow technically incorrect requirements.

OUTPUT:
Give a direct answer first.
Then provide reasoning, implementation details or relevant
references when necessary.
"""


def check_api_key():
    try:
        config.openrouter_api_keys()

    except RuntimeError as error:
        print(f"\n[ERROR] {error}")
        sys.exit(1)


def build_document_context(text):

    print("\n[2/4] Processing organization knowledge...")

    chunks = chunk_text(text)

    print(f"      Extracted {len(text):,} characters")

    print(f"      Created {len(chunks)} knowledge chunks")

    max_context_chars = 30000

    context = text[:max_context_chars]

    return context


def call_model(messages):
    try:
        data = llm.openrouter_chat(messages, model=MODEL)
        response, _, _ = llm.extract_message(data)
        return response or None

    except llm.LLMError as error:
        print(f"[ERROR] OpenRouter request failed: {error}")

        return None


def initialize_ai(document_context):

    print("\n[3/4] Initializing AI workbench...")

    organization_prompt = f"""
ORGANIZATION KNOWLEDGE BASE
============================

The following information was extracted from documents
provided by the organization.

Use this information as organization-specific context.

IMPORTANT:
- Do not treat this as universally true knowledge.
- Use it only when relevant.
- If the answer cannot be found here, say that it is not
  available in the provided organization knowledge.

--- BEGIN ORGANIZATION DATA ---

{document_context}

--- END ORGANIZATION DATA ---
"""

    messages = [

        {
            "role": "system",
            "content": SYSTEM_PROMPT
        },

        {
            "role": "system",
            "content": organization_prompt
        }
    ]

    test_message = {

        "role": "user",

        "content": """
Confirm that the AI workbench has been initialized.

Respond with:
1. Model status
2. Organization knowledge status
3. Your role
4. Whether you are ready for tasks
"""
    }

    messages.append(test_message)

    response = call_model(messages)

    if response:

        print("\n================ AI INITIALIZED ================\n")

        print(response)

        print("\n=================================================\n")

        messages.pop()

        return messages

    return None


def chat_loop(messages):

    print("\n[4/4] Workbench ready.")

    print("\nCommands:")
    print("  /exit   → Exit")
    print("  /clear  → Clear conversation")
    print("\n")

    while True:

        try:

            user_input = input("You: ").strip()

        except KeyboardInterrupt:

            print("\n\nExiting...")

            break

        if not user_input:

            continue

        if user_input.lower() == "/exit":

            print("\nAI Workbench stopped.")

            break

        if user_input.lower() == "/clear":
            messages = messages[:2]

            print("\nConversation cleared.\n")

            continue

        messages.append({

            "role": "user",

            "content": user_input
        })

        print("\nAI: ", end="", flush=True)

        response = call_model(messages)

        if response:

            print(response)

            messages.append({

                "role": "assistant",

                "content": response
            })

        else:

            print("Unable to get a response.")

            messages.pop()


def ingest_source(source_path, db_path=None, log=print):
    """Extract a PDF or text file into the searchable knowledge database."""
    if not os.path.exists(source_path):
        raise ValueError(f"Source file does not exist: {source_path}")

    suffix = os.path.splitext(source_path)[1].lower()

    if suffix == ".pdf":
        return knowledge.ingest_pdf(source_path, db_path=db_path, log=log)

    with open(source_path, "r", encoding="utf-8", errors="replace") as handle:
        text = knowledge.clean_text(handle.read())

    if not text:
        raise ValueError(f"No text could be extracted from {source_path}.")

    chunks = knowledge.chunk_text(text)
    title = os.path.basename(source_path)

    return [
        knowledge.add_document(
            source=source_path,
            title=f"{title} [{index}/{len(chunks)}]",
            content=chunk,
            db_path=db_path,
        )
        for index, chunk in enumerate(chunks, 1)
    ]


def main():
    print("\nUpload your organization knowledge.")
    source_path = input("Enter PDF or text file path: ").strip()

    if not source_path:
        print("[ERROR] No source path provided.")
        return 1

    try:
        ids = ingest_source(source_path)
    except (OSError, ValueError) as error:
        print(f"\n[ERROR] {error}")
        return 1

    print(f"\nStored {len(ids)} knowledge chunk(s) in knowledge.db.")
    print("Starting modelrouter chat...\n")
    modelrouter.banner()
    return modelrouter.interactive()


if __name__ == "__main__":

    main()
