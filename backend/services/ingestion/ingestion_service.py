from dataclasses import dataclass
import io
import re
from typing import List, Optional
from docx import Document as DocxDocument
from pypdf import PdfReader


@dataclass
class ParsedSection:
    text: str
    section: str
    page_number: Optional[int] = None

    def get_citation_label(self, doc_title: str) -> str:
        """
        Formats citation accurately without inventing fake page numbers (Decision D15):
        - For PDFs with real pages: '[Document Title, p. 3]'
        - For Markdown / DOCX / TXT: '[Document Title, Section: Leave Entitlements]'
        """
        if self.page_number is not None:
            return f"[{doc_title}, p. {self.page_number}]"
        return f"[{doc_title}, Section: {self.section}]"


def parse_text(content: bytes) -> List[ParsedSection]:
    """Parse raw .txt files, identifying section headers without inventing fake page numbers."""
    text = content.decode("utf-8", errors="replace").strip()
    if not text:
        return []

    lines = text.splitlines()
    sections: List[ParsedSection] = []
    current_section = "General Overview"
    current_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        is_heading = (
            (stripped.isupper() and len(stripped) < 80 and not stripped.endswith("."))
            or bool(re.match(r"^(SECTION\s+\d+|ARTICLE\s+\d+|\d+\.\s+[A-Z])", stripped, re.IGNORECASE))
        )
        if is_heading:
            if current_lines:
                sec_text = "\n".join(current_lines).strip()
                if sec_text:
                    sections.append(ParsedSection(text=sec_text, section=current_section, page_number=None))
                current_lines = []
            current_section = stripped.rstrip(":")
        else:
            current_lines.append(line)

    if current_lines:
        sec_text = "\n".join(current_lines).strip()
        if sec_text:
            sections.append(ParsedSection(text=sec_text, section=current_section, page_number=None))

    if not sections and text.strip():
        # Fallback if markdown contains only header lines without trailing paragraph text
        sections.append(ParsedSection(text=text.strip(), section=current_section, page_number=None))

    return sections


def parse_markdown(content: bytes) -> List[ParsedSection]:
    """Parse .md files, cleanly grouping content under markdown headers (# and ##)."""
    text = content.decode("utf-8", errors="replace").strip()
    if not text:
        return []

    lines = text.splitlines()
    sections: List[ParsedSection] = []
    current_section = "Introduction"
    current_lines: List[str] = []

    for line in lines:
        header_match = re.match(r"^#{1,3}\s+(.+)$", line)
        if header_match:
            if current_lines:
                sec_text = "\n".join(current_lines).strip()
                if sec_text:
                    sections.append(ParsedSection(text=sec_text, section=current_section, page_number=None))
                current_lines = []
            current_section = header_match.group(1).strip()
        else:
            current_lines.append(line)

    if current_lines:
        sec_text = "\n".join(current_lines).strip()
        if sec_text:
            sections.append(ParsedSection(text=sec_text, section=current_section, page_number=None))

    if not sections and text.strip():
        # Fallback if markdown contains only header lines without trailing paragraph text
        sections.append(ParsedSection(text=text.strip(), section=current_section, page_number=None))

    return sections


def parse_pdf(content: bytes) -> List[ParsedSection]:
    """Parse .pdf files tracking true physical page numbers for citations (Decision D15)."""
    reader = PdfReader(io.BytesIO(content))
    pages: List[ParsedSection] = []
    for idx, page in enumerate(reader.pages):
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(ParsedSection(
                text=page_text.strip(),
                section=f"Page {idx + 1}",
                page_number=idx + 1,
            ))
    return pages


def parse_docx(content: bytes) -> List[ParsedSection]:
    """Parse .docx files extracting headings, paragraphs, and tables."""
    doc = DocxDocument(io.BytesIO(content))
    sections: List[ParsedSection] = []
    current_section = "Overview"
    current_paragraphs: List[str] = []

    for para in doc.paragraphs:
        style_name = getattr(para.style, "name", "") or ""
        if style_name.startswith("Heading"):
            if current_paragraphs:
                sec_text = "\n".join(current_paragraphs).strip()
                if sec_text:
                    sections.append(ParsedSection(text=sec_text, section=current_section, page_number=None))
                current_paragraphs = []
            current_section = para.text.strip()
        elif para.text.strip():
            current_paragraphs.append(para.text.strip())

    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join([cell.text.strip() for cell in row.cells if cell.text.strip()])
            if row_text:
                current_paragraphs.append(row_text)

    if current_paragraphs:
        sec_text = "\n".join(current_paragraphs).strip()
        if sec_text:
            sections.append(ParsedSection(text=sec_text, section=current_section, page_number=None))

    return sections


def parse_file(filename: str, content: bytes) -> List[ParsedSection]:
    """Dispatch content parsing based on file extension."""
    lower_name = filename.lower()
    if lower_name.endswith(".md"):
        return parse_markdown(content)
    elif lower_name.endswith(".txt"):
        return parse_text(content)
    elif lower_name.endswith(".pdf"):
        return parse_pdf(content)
    elif lower_name.endswith(".docx"):
        return parse_docx(content)
    return parse_text(content)
