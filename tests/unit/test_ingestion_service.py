from pathlib import Path
import pytest

from backend.services.chunking_service import chunk_sections, stamp_identity_header
from backend.services.ingestion_service import parse_docx, parse_file, parse_markdown, parse_text

SAMPLE_DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sample_documents"


def test_parse_markdown_sections():
    md_file = SAMPLE_DOCS_DIR / "ENG_Travel_and_Offsite_Policy.md"
    assert md_file.exists()
    content = md_file.read_bytes()

    sections = parse_markdown(content)
    assert len(sections) > 1
    # Check that sections are named from headers and page_number is None
    section_titles = [s.section for s in sections]
    assert any("Daily Meal Allowance" in title or "Allowance" in title for title in section_titles)
    assert all(s.page_number is None for s in sections)

    # Check citation label
    citation = sections[0].get_citation_label("Engineering Travel Policy")
    assert citation.startswith("[Engineering Travel Policy, Section:")


def test_parse_text_sections():
    txt_file = SAMPLE_DOCS_DIR / "HR_POSH_and_Confidential_Code_of_Conduct.txt"
    assert txt_file.exists()
    content = txt_file.read_bytes()

    sections = parse_text(content)
    assert len(sections) >= 1
    for s in sections:
        assert s.page_number is None
        assert s.text


def test_parse_docx_file():
    docx_file = SAMPLE_DOCS_DIR / "ENG_Travel_and_Offsite_Policy.docx"
    assert docx_file.exists()
    content = docx_file.read_bytes()

    sections = parse_docx(content)
    assert len(sections) >= 1
    # Check table content or paragraphs were extracted
    all_text = " ".join(s.text for s in sections)
    assert "2,500" in all_text or "Bengaluru" in all_text


def test_chunking_and_identity_header_stamping():
    md_file = SAMPLE_DOCS_DIR / "ENG_Travel_and_Offsite_Policy.md"
    content = md_file.read_bytes()
    sections = parse_file("ENG_Travel_and_Offsite_Policy.md", content)

    chunks = chunk_sections(
        sections=sections,
        title="Eng Travel",
        version=1,
        department="Engineering",
        chunk_size=400,
        chunk_overlap=50,
    )

    assert len(chunks) > 0
    first_chunk = chunks[0]
    # Verify D6 Identity Header is stamped
    assert first_chunk.stamped_content.startswith("[Document: Eng Travel | Version: 1 | Department: Engineering | Section:")
    assert "Section:" in first_chunk.citation
