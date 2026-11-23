
"""
Resume text extraction for HireWise AI.

Supports PDF and DOCX files from:
- Local file paths
- Database-stored file bytes

AI processing is handled separately by ai_service.py.
"""

import re
from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfReader


# =========================================================
# TEXT CLEANING
# =========================================================

def clean_extracted_text(text):
    """Clean extracted text without removing useful contact details."""

    if not text:
        return ""

    text = text.replace("\x00", " ")
    text = text.replace("\u00a0", " ")

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# =========================================================
# COMMON FILE SOURCE HELPER
# =========================================================

def prepare_file_source(file_source):
    """Prepare either a local file path or an open binary stream."""

    if hasattr(file_source, "read"):

        if hasattr(file_source, "seek"):
            file_source.seek(0)

        return file_source

    return str(file_source)


# =========================================================
# PDF TEXT EXTRACTION
# =========================================================

def extract_pdf_text(file_source):
    """Extract text from every page of a PDF resume."""

    source = prepare_file_source(file_source)

    try:
        reader = PdfReader(source)

    except Exception as error:
        raise ValueError(
            "Unable to open the PDF file. "
            "Please upload a valid PDF document."
        ) from error

    if reader.is_encrypted:
        raise ValueError(
            "Password-protected PDF files are not supported."
        )

    pages = []

    for page_number, page in enumerate(reader.pages, start=1):

        try:
            try:
                page_text = page.extract_text(
                    extraction_mode="layout"
                ) or ""

            except TypeError:
                # Compatibility with versions that do not support
                # extraction_mode="layout".
                page_text = page.extract_text() or ""

        except Exception as error:
            raise ValueError(
                f"Unable to extract text from PDF page {page_number}."
            ) from error

        page_text = clean_extracted_text(page_text)

        if page_text:
            pages.append({
                "page": page_number,
                "text": page_text,
            })

    if not pages:
        raise ValueError(
            "No readable text found in this PDF. "
            "It may be a scanned image or contain text "
            "that cannot be extracted."
        )

    return pages


# =========================================================
# DOCX TEXT EXTRACTION
# =========================================================

def extract_docx_text(file_source):
    """
    Extract text from DOCX paragraphs, tables,
    headers and footers.
    """

    source = prepare_file_source(file_source)

    try:
        document = Document(source)

    except Exception as error:
        raise ValueError(
            "Unable to open the DOCX file. "
            "Please upload a valid DOCX document."
        ) from error

    sections = []

    # ----------------------------------------------
    # EXTRACT DOCUMENT HEADERS
    # ----------------------------------------------

    for section in document.sections:

        for paragraph in section.header.paragraphs:

            text = clean_extracted_text(paragraph.text)

            if text:
                sections.append(text)

        for table in section.header.tables:

            for row in table.rows:

                for cell in row.cells:

                    text = clean_extracted_text(cell.text)

                    if text:
                        sections.append(text)

    # ----------------------------------------------
    # EXTRACT NORMAL PARAGRAPHS
    # ----------------------------------------------

    for paragraph in document.paragraphs:

        text = clean_extracted_text(paragraph.text)

        if text:
            sections.append(text)

    # ----------------------------------------------
    # EXTRACT TABLES
    # ----------------------------------------------

    for table in document.tables:

        for row in table.rows:

            for cell in row.cells:

                text = clean_extracted_text(cell.text)

                if text:
                    sections.append(text)

    # ----------------------------------------------
    # EXTRACT DOCUMENT FOOTERS
    # ----------------------------------------------

    for section in document.sections:

        for paragraph in section.footer.paragraphs:

            text = clean_extracted_text(paragraph.text)

            if text:
                sections.append(text)

        for table in section.footer.tables:

            for row in table.rows:

                for cell in row.cells:

                    text = clean_extracted_text(cell.text)

                    if text:
                        sections.append(text)

    if not sections:
        raise ValueError(
            "No readable text found in this DOCX file."
        )

    return [{
        "page": None,
        "text": "\n".join(sections),
    }]


# =========================================================
# LOCAL FILE EXTRACTION
# =========================================================

def extract_resume_text(file_path):
    """
    Extract text from a local PDF or DOCX resume.

    Returns a list of sections containing extracted text.
    """

    if not file_path:
        raise ValueError("Resume file path is missing.")

    path = Path(file_path)

    if not path.is_file():
        raise FileNotFoundError(
            "Resume file was not found."
        )

    extension = path.suffix.lower()

    if extension == ".pdf":
        return extract_pdf_text(path)

    if extension == ".docx":
        return extract_docx_text(path)

    raise ValueError(
        "Only PDF and DOCX files are supported."
    )


def get_full_resume_text(file_path):
    """Combine extracted sections from a local resume."""

    sections = extract_resume_text(file_path)

    return "\n\n".join(
        section["text"]
        for section in sections
        if section.get("text")
    )


# =========================================================
# DATABASE FILE BYTE EXTRACTION
# =========================================================

def extract_resume_text_from_bytes(file_data, filename):
    """
    Extract text directly from database-stored resume bytes.

    Supports PDF and DOCX files without creating local files.
    """

    if not file_data:
        raise ValueError(
            "Resume file is empty."
        )

    if not filename or not str(filename).strip():
        raise ValueError(
            "Resume filename is missing."
        )

    if not isinstance(file_data, (bytes, bytearray, memoryview)):
        raise ValueError(
            "Invalid resume file data."
        )

    file_data = bytes(file_data)

    if not file_data:
        raise ValueError(
            "Resume file is empty."
        )

    extension = Path(str(filename)).suffix.lower()

    file_stream = BytesIO(file_data)

    if extension == ".pdf":
        return extract_pdf_text(file_stream)

    if extension == ".docx":
        return extract_docx_text(file_stream)

    raise ValueError(
        "Only PDF and DOCX files are supported."
    )


def get_full_resume_text_from_bytes(file_data, filename):
    """
    Combine extracted sections from database-stored resume bytes.
    """

    sections = extract_resume_text_from_bytes(
        file_data,
        filename,
    )

    return "\n\n".join(
        section["text"]
        for section in sections
        if section.get("text")
    )
