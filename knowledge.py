"""Local knowledge base for the reasoning stage.

SQLite with an FTS5 index. Retrieval happens here in Python and the hits are
injected into the reasoning prompt rather than exposed as a model-callable
tool: free-tier models invoke tools unreliably, and a dropped call degrades
the answer with no visible error. Retrieving locally is both reliable and
cheaper.
"""

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config


SCHEMA = """ 
CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title,
    content,
    content='documents',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, title, content)
    VALUES (new.id, new.title, new.content);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content)
    VALUES ('delete', old.id, old.title, old.content);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, title, content)
    VALUES ('delete', old.id, old.title, old.content);
    INSERT INTO documents_fts(rowid, title, content)
    VALUES (new.id, new.title, new.content);
END;
"""


# ============================================================
# CONNECTION
# ============================================================

def connect(db_path=None):
    connection = sqlite3.connect(Path(db_path) if db_path else config.KNOWLEDGE_DB)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def init_db(db_path=None):
    connect(db_path).close()


# ============================================================
# WRITING
# ============================================================

def add_document(source, title, content, db_path=None):
    content = (content or "").strip()

    if not content:
        raise ValueError("Refusing to store an empty document.")

    connection = connect(db_path)

    try:
        cursor = connection.execute(
            """
            INSERT INTO documents (source, title, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                source,
                title or source,
                content,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
        return cursor.lastrowid

    finally:
        connection.close()


def document_count(db_path=None):
    connection = connect(db_path)

    try:
        return connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    finally:
        connection.close()


# ============================================================
# SEARCH
# ============================================================

def _fts_expression(text):
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Raw user input cannot go into MATCH directly - punctuation like quotes,
    asterisks and parentheses are FTS5 operators and raise OperationalError.
    Each word is quoted as a literal prefix query and OR-ed together. Prefix
    matching matters because FTS5 does no stemming: without it a search for
    "product" would not find a document containing "products".

    The match is one-directional. The query must be a prefix of the stored
    token, never the other way round: "product"* finds "products", but
    "products"* does not find "product".
    """
    terms = re.findall(r"[0-9A-Za-z_]+", text)

    if not terms:
        return None

    return " OR ".join(f'"{term}"*' for term in terms)


def search(query, limit=5, db_path=None):
    """Return the best matching documents for a query, best first."""
    query = (query or "").strip()

    if not query:
        return []

    connection = connect(db_path)

    try:
        expression = _fts_expression(query)

        if expression:
            rows = connection.execute(
                """
                SELECT d.id, d.source, d.title, d.content, bm25(documents_fts) AS rank
                FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                WHERE documents_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (expression, limit),
            ).fetchall()

            if rows:
                return [dict(row) for row in rows]

        # FTS matches whole tokens only. Fall back to a substring scan so a
        # query against text that was never tokenised cleanly still returns.
        pattern = f"%{query}%"
        rows = connection.execute(
            """
            SELECT id, source, title, content
            FROM documents
            WHERE title LIKE ? OR content LIKE ?
            LIMIT ?
            """,
            (pattern, pattern, limit),
        ).fetchall()

        return [dict(row) for row in rows]

    finally:
        connection.close()


def context_for(query, limit=5, db_path=None):
    """Return knowledge-base hits formatted for injection into a prompt."""
    hits = search(query, limit=limit, db_path=db_path)

    if not hits:
        return ""

    blocks = [
        f"[{index}] {hit['title']}\n(source: {hit['source']})\n{hit['content']}"
        for index, hit in enumerate(hits, 1)
    ]

    return "\n\n".join(blocks)


# ============================================================
# DOCUMENT INGESTION
# ============================================================

def clean_text(text):
    text = re.sub(r"[ \t]+", " ", text)

    text = re.sub(r"\n\s*\n+", "\n\n", text)

    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

    return text.strip()


def chunk_text(text, chunk_size=8000):
    chunks = []
    start = 0

    while start < len(text):
        chunks.append(text[start:start + chunk_size])
        start += chunk_size

    return chunks


def extract_pdf_text(pdf_path, log=print):
    """Extract text from a PDF, falling back to OCR for scanned pages.

    PyMuPDF and pytesseract are imported here rather than at module scope so
    that querying an existing knowledge base does not require them.
    """
    import pymupdf
    import pytesseract
    from PIL import Image

    if not os.path.exists(pdf_path):
        raise ValueError(f"PDF file does not exist: {pdf_path}")

    log(f"  Reading {pdf_path}...")

    document = pymupdf.open(pdf_path)
    extracted = []

    try:
        for page_number, page in enumerate(document):
            text = page.get_text("text").strip()

            if text:
                extracted.append(text)
                continue

            log(f"  Page {page_number + 1}: no embedded text, running OCR...")

            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))

            image = Image.frombytes(
                "RGB",
                [pixmap.width, pixmap.height],
                pixmap.samples,
            )

            extracted.append(pytesseract.image_to_string(image))

    finally:
        document.close()

    return clean_text("\n\n".join(extracted))


def ingest_pdf(pdf_path, title=None, db_path=None, log=print):
    """Extract a PDF and store it in the knowledge base, chunk by chunk."""
    text = extract_pdf_text(pdf_path, log=log)

    if not text:
        raise ValueError(f"No text could be extracted from {pdf_path}.")

    name = title or os.path.basename(pdf_path)
    chunks = chunk_text(text)

    log(f"  {len(text):,} characters -> {len(chunks)} chunk(s)")

    return [
        add_document(
            source=pdf_path,
            title=f"{name} [{index}/{len(chunks)}]",
            content=chunk,
            db_path=db_path,
        )
        for index, chunk in enumerate(chunks, 1)
    ]


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Manage the local knowledge base."
    )
    parser.add_argument("--ingest", metavar="PDF", help="Ingest a PDF")
    parser.add_argument("--search", metavar="QUERY", help="Search the knowledge base")
    parser.add_argument("-n", "--limit", type=int, default=5)
    args = parser.parse_args()

    init_db()

    if args.ingest:
        ids = ingest_pdf(args.ingest)
        print(f"Stored {len(ids)} chunk(s). Total documents: {document_count()}")

    if args.search:
        hits = search(args.search, limit=args.limit)

        if not hits:
            print("No matches.")
            return

        for hit in hits:
            print(f"\n--- {hit['title']} ---")
            print(f"source: {hit['source']}")
            print(hit["content"][:400])

    if not args.ingest and not args.search:
        parser.print_help()


if __name__ == "__main__":
    main()
