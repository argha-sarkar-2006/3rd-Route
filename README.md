# MMAR - Multimodal Multi-Agent Workbench

A multi-stage AI workbench that routes and processes tasks across vision analysis, knowledge base retrieval, web-augmented reasoning, and code generation.

## Pipeline Architecture

- Vision Stage: Analyzes images, diagrams, and error screenshots with local `qwen3.5:2b`.
- Reasoning Stage: Performs local RAG and best-effort web search with local `qwen3.5:2b`.
- Coding Stage: Implements working solutions with local `aikid123/qwen3-coder:0.6b`.
- Router: Automatically determines task requirements and coordinates stage execution.

## Prerequisites

- Python 3.10 or higher
- Tesseract OCR (required for OCR and image text extraction)
  - Ubuntu/Debian: `sudo apt-get install tesseract-ocr`
  - macOS: `brew install tesseract`
  - Windows: Install via the official installer and add the installation folder to your PATH.

## Setup

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd MMAR
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   ```
   - On Windows:
     ```powershell
     .venv\Scripts\activate
     ```
   - On Linux/macOS:
     ```bash
     source .venv/bin/activate
     ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment variables:
   Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and provide your API keys:
   - `OLLAMA_API_KEY`: Optional key for an authenticated Ollama endpoint

## Usage

### Interactive Mode
Run the router interactively:
```bash
python modelrouter.py
```

### CLI Mode
Submit a text query directly:
```bash
python modelrouter.py --text "Explain how vector databases work."
```

Submit an image with an optional prompt:
```bash
python modelrouter.py --image "path/to/diagram.png" --prompt "Explain this architecture"
```

Ingest a document into the local knowledge base:
```bash
python knowledge.py --ingest "path/to/document.pdf"
```

## Project Structure

- `modelrouter.py`: Central orchestrator and task router.
- `vision.py`: Image analysis and OCR pipeline.
- `reasoning.py`: Web research, document RAG, and problem synthesis.
- `coding.py`: Automated code generation.
- `knowledge.py`: Document ingestion, chunking, and SQLite storage.
- `llm.py`: Shared local Ollama transport.
- `config.py`: Centralized configuration, endpoints, and credentials loader.
