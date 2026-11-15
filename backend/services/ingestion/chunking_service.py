from dataclasses import dataclass
from typing import List, Optional

from backend.services.ingestion.ingestion_service import ParsedSection


@dataclass
class DocumentChunk:
    text: str
    section: str
    page_number: Optional[int]
    citation: str
    stamped_content: str  # Vector-ready content with D6 identity header


def stamp_identity_header(title: str, version: int, department: str, section: str, text: str) -> str:
    """
    Prepends Contextual Identity Header to anchor the chunk mathematically (Decision D6).
    """
    header = f"[Document: {title} | Version: {version} | Department: {department} | Section: {section}]"
    return f"{header}\n\n{text}"


def chunk_sections(
    sections: List[ParsedSection],
    title: str,
    version: int,
    department: str,
    chunk_size: int = 500,
    chunk_overlap: int = 100,
) -> List[DocumentChunk]:
    """
    Takes parsed sections, splits long sections with character overlap,
    and prepends Decision D6 Contextual Identity Headers.
    """
    chunks: List[DocumentChunk] = []

    for section_info in sections:
        raw_text = section_info.text
        page_num = section_info.page_number
        section_name = section_info.section
        citation_str = section_info.get_citation_label(title)

        # If section fits within chunk_size, keep as single chunk
        if len(raw_text) <= chunk_size:
            stamped = stamp_identity_header(title, version, department, section_name, raw_text)
            chunks.append(
                DocumentChunk(
                    text=raw_text,
                    section=section_name,
                    page_number=page_num,
                    citation=citation_str,
                    stamped_content=stamped,
                )
            )
        else:
            # Sliding window with overlap
            start = 0
            while start < len(raw_text):
                end = start + chunk_size
                chunk_slice = raw_text[start:end].strip()
                if chunk_slice:
                    stamped = stamp_identity_header(title, version, department, section_name, chunk_slice)
                    chunks.append(
                        DocumentChunk(
                            text=chunk_slice,
                            section=section_name,
                            page_number=page_num,
                            citation=citation_str,
                            stamped_content=stamped,
                        )
                    )
                start += (chunk_size - chunk_overlap)

    return chunks
