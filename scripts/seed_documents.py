import asyncio
import json
import os
from pathlib import Path
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException
from sqlmodel import select

from backend.db.database import async_session, init_db
from backend.db.qdrant import get_qdrant_client, init_qdrant_collection
from backend.models.document import Document
from backend.models.enums import ClearanceLevel, DocumentStatus
from backend.services.chunking_service import chunk_sections
from backend.services.document_service import (
    compute_file_hash,
    finalize_document_activation,
    mark_document_failed,
    register_document_upload,
)
from backend.services.ingestion_service import parse_file
from backend.services.vector_service import (
    activate_document_version,
    index_document_chunks,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "sample_documents"
MANIFEST_PATH = DATA_DIR / "manifest.json"

SUPPORTED_EXTENSIONS = {".md", ".docx", ".pdf", ".txt"}


def infer_metadata_from_filename(filename: str) -> dict:
    """Fallback metadata inference if file is not in manifest.json."""
    upper = filename.upper()
    dept = "General"
    if upper.startswith("ENG_") or "ENGINEERING" in upper:
        dept = "Engineering"
    elif upper.startswith("HR_"):
        dept = "HR"
    elif upper.startswith("FIN_") or "FINANCE" in upper:
        dept = "Finance"

    clearance = ClearanceLevel.INTERNAL
    if "CONFIDENTIAL" in upper or "VAULT" in upper or "SECURITY" in upper:
        clearance = ClearanceLevel.CONFIDENTIAL
    elif "RESTRICTED" in upper:
        clearance = ClearanceLevel.RESTRICTED
    elif "PUBLIC" in upper:
        clearance = ClearanceLevel.PUBLIC

    title = filename.rsplit(".", 1)[0].replace("_", " ")
    return {
        "title": title,
        "department": dept,
        "clearance_level": clearance,
    }


async def seed_documents():
    """
    Seeds all documents in the data folder into Cortex SQLModel DB and Qdrant vector store.
    Pipeline:
    1. Ensures DB and Qdrant collection exist.
    2. Reads manifest.json (or infers metadata).
    3. Checks SHA-256 hash for deduplication. Skips if already active.
    4. Parses document (MD, DOCX, TXT, PDF).
    5. Chunks with sliding window (500 chars / 100 overlap) + identity headers.
    6. Generates Dense (384d) + Sparse (BM25) embeddings.
    7. Indexes chunks in Qdrant staged (is_live=False).
    8. Activates document in DB (is_live=True) and flips Qdrant vectors.
    """
    print("=" * 70)
    print(" Cortex Document Seeder (Local Data Folder -> Qdrant + DB)")
    print("=" * 70)

    # 1. Initialize DB and Qdrant
    await init_db()
    qdrant_client = get_qdrant_client()
    init_qdrant_collection(qdrant_client)

    # 2. Load manifest if available
    manifest_map = {}
    if MANIFEST_PATH.exists():
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)
                for item in manifest_data.get("documents", []):
                    manifest_map[item["filename"]] = item
            print(f"[INIT] Loaded manifest with {len(manifest_map)} entries from {MANIFEST_PATH.name}")
        except Exception as e:
            print(f"[WARN] Failed to read manifest.json: {e}")

    # 3. Discover all supported files in data directory
    if not DATA_DIR.exists():
        print(f"[ERROR] Data directory does not exist: {DATA_DIR}")
        return

    files_to_process = [
        f for f in DATA_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    print(f"[DISCOVERY] Found {len(files_to_process)} document(s) in {DATA_DIR}\n")

    total_indexed = 0
    total_skipped = 0
    total_chunks = 0

    async with async_session() as session:
        for file_path in files_to_process:
            filename = file_path.name
            content = file_path.read_bytes()
            file_hash = compute_file_hash(content)

            # Metadata resolution
            meta = manifest_map.get(filename)
            if meta:
                title = meta.get("title", filename)
                department = meta.get("department", "General")
                clearance_raw = meta.get("clearance_level", "Internal")
                try:
                    clearance_level = ClearanceLevel(clearance_raw)
                except ValueError:
                    clearance_level = ClearanceLevel.INTERNAL
            else:
                inferred = infer_metadata_from_filename(filename)
                title = inferred["title"]
                department = inferred["department"]
                clearance_level = inferred["clearance_level"]

            # Clean up any previous failed attempts for this file so it can retry
            stmt_failed = select(Document).where(
                (Document.file_hash == file_hash) | (Document.filename == filename),
                Document.status == DocumentStatus.UPDATE_FAILED,
            )
            res_failed = await session.execute(stmt_failed)
            for failed_doc in res_failed.scalars().all():
                await session.delete(failed_doc)
            await session.commit()

            # Idempotency check: see if exact hash is already stored & active
            stmt_hash = select(Document).where(Document.file_hash == file_hash)
            res_hash = await session.execute(stmt_hash)
            existing_doc = res_hash.scalars().first()

            if existing_doc and existing_doc.is_live:
                print(f"[SKIP] '{filename}' is already indexed and active (v{existing_doc.version}, id={existing_doc.id})")
                total_skipped += 1
                continue

            print(f"[PROCESSING] '{filename}'")
            print(f"             Dept: {department} | Clearance: {clearance_level.value} | Title: {title}")

            # Register document in database (staged)
            try:
                doc, is_new_version = await register_document_upload(
                    session=session,
                    filename=filename,
                    content=content,
                    department=department,
                    clearance_level=clearance_level,
                    title=title,
                )
            except HTTPException as exc:
                if exc.status_code == 409:
                    print(f"             [SKIP] SHA-256 duplicate conflict: {exc.detail}")
                    total_skipped += 1
                    continue
                else:
                    print(f"             [ERROR] DB registration error: {exc.detail}")
                    continue

            # Ingestion pipeline: Parse -> Chunk -> Vector Index -> Flip
            try:
                sections = parse_file(filename, content)
                chunks = chunk_sections(
                    sections=sections,
                    title=doc.title,
                    version=doc.version,
                    department=doc.department,
                )

                if not chunks:
                    print(f"             [WARN] No text chunks extracted from '{filename}'")
                    await mark_document_failed(session, doc.id, "No extractable text chunks found")
                    continue

                # Stage index in Qdrant (is_live=False)
                num_indexed = index_document_chunks(
                    client=qdrant_client,
                    document_id=doc.id,
                    version=doc.version,
                    department=doc.department,
                    clearance_level=doc.clearance_level,
                    chunks=chunks,
                    is_live=False,
                )

                # Atomic switchover in SQL and Qdrant
                activate_document_version(
                    client=qdrant_client,
                    document_id=doc.id,
                    active_version=doc.version,
                )
                await finalize_document_activation(session, doc.id)

                print(f"             [SUCCESS] Indexed {num_indexed} chunks -> Active (v{doc.version})\n")
                total_indexed += 1
                total_chunks += num_indexed

            except Exception as exc:
                print(f"             [ERROR] Ingestion failed for '{filename}': {exc}\n")
                await mark_document_failed(session, doc.id, str(exc))

    print("=" * 70)
    print(f" Seeding Summary: {total_indexed} indexed ({total_chunks} chunks), {total_skipped} skipped")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(seed_documents())
