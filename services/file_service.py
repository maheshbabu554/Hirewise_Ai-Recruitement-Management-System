
"""
Secure file handling for HireWise AI.

Supports:
- Resume validation (PDF and DOCX)
- Candidate document storage (PDF, JPG, JPEG and PNG)
- Database-backed private file storage using StoredFile
- Legacy filesystem helpers for backward compatibility
"""

import uuid
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from models import StoredFile


# =========================================================
# FILE CONFIGURATION
# =========================================================

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".docx",
}

ALLOWED_DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
}

ALLOWED_STORAGE_EXTENSIONS = (
    ALLOWED_EXTENSIONS | ALLOWED_DOCUMENT_EXTENSIONS
)

MAX_FILE_SIZE = 5 * 1024 * 1024

MAX_BATCH_FILES = 20


# =========================================================
# RESUME VALIDATION
# =========================================================

def validate_resume(file):
    """Validate the uploaded resume's name, extension and size."""

    if not file or not file.filename:
        raise ValueError("No file was selected.")

    original_name = secure_filename(file.filename)

    if not original_name:
        raise ValueError("Invalid filename.")

    extension = Path(original_name).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(
            "Only PDF and DOCX files are allowed."
        )

    file.stream.seek(0, 2)
    file_size = file.stream.tell()
    file.stream.seek(0)

    if file_size == 0:
        raise ValueError(
            "The uploaded file is empty."
        )

    if file_size > MAX_FILE_SIZE:
        raise ValueError(
            "Each resume must be 5 MB or smaller."
        )

    return original_name, extension


# =========================================================
# DATABASE FILE STORAGE
# =========================================================

def create_stored_file(
    file,
    original_filename=None,
    mime_type=None,
):
    """
    Create a database-backed StoredFile object.

    The returned object is not committed automatically.
    The calling route must add it to the SQLAlchemy session
    and commit it with the related candidate/document record.
    """

    if not file or not file.filename:
        raise ValueError(
            "No file was selected."
        )

    filename = secure_filename(
        original_filename or file.filename
    )

    if not filename:
        raise ValueError(
            "Invalid filename."
        )

    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_STORAGE_EXTENSIONS:
        raise ValueError(
            "Unsupported file format."
        )

    # Check file size before reading the complete content.
    file.stream.seek(0, 2)
    file_size = file.stream.tell()
    file.stream.seek(0)

    if file_size == 0:
        raise ValueError(
            "The uploaded file is empty."
        )

    if file_size > MAX_FILE_SIZE:
        raise ValueError(
            "Each file must be 5 MB or smaller."
        )

    # Read the binary data into memory.
    try:
        file.stream.seek(0)
        file_data = file.stream.read(MAX_FILE_SIZE + 1)
    finally:
        file.stream.seek(0)

    if not file_data:
        raise ValueError(
            "The uploaded file is empty."
        )

    # Recheck actual bytes read.
    if len(file_data) > MAX_FILE_SIZE:
        raise ValueError(
            "Each file must be 5 MB or smaller."
        )

    # Keep the filename within the database column limit.
    suffix = Path(filename).suffix
    if len(filename) > 255:
        filename = (
            Path(filename).stem[:255 - len(suffix)]
            + suffix
        )

    safe_mime_type = (
        mime_type
        or file.mimetype
        or "application/octet-stream"
    )

    safe_mime_type = str(safe_mime_type).strip()[:100]

    return StoredFile(
        file_data=file_data,
        original_filename=filename,
        mime_type=safe_mime_type,
        file_size=len(file_data),
    )


# =========================================================
# LEGACY RESUME STORAGE
# =========================================================

def save_resume(file):
    """
    Save a resume to the legacy local filesystem.

    Retained only for backward compatibility.
    New resume uploads should use create_stored_file().
    """

    original_name, extension = validate_resume(file)

    upload_folder = Path(
        current_app.config["RESUME_UPLOAD_FOLDER"]
    ).resolve()

    upload_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    unique_filename = (
        f"{uuid.uuid4().hex}{extension}"
    )

    destination = upload_folder / unique_filename

    file.save(destination)

    relative_path = f"resumes/{unique_filename}"

    return {
        "original_name": original_name,
        "stored_filename": unique_filename,
        "relative_path": relative_path,
        "absolute_path": str(destination),
    }


def delete_resume(file_path):
    """
    Safely delete a legacy locally stored resume.

    This does not delete database-backed StoredFile records.
    """

    if not file_path:
        return

    upload_folder = Path(
        current_app.config["RESUME_UPLOAD_FOLDER"]
    ).resolve()

    filename = Path(file_path).name

    target = (
        upload_folder / filename
    ).resolve()

    if target.parent == upload_folder and target.is_file():
        target.unlink()


# =========================================================
# BULK UPLOAD VALIDATION
# =========================================================

def validate_batch_count(files):
    """Ensure a batch contains between 1 and 20 selected files."""

    if not files:
        raise ValueError(
            "Please select at least one resume."
        )

    if len(files) > MAX_BATCH_FILES:
        raise ValueError(
            "You can upload a maximum of 20 resumes at a time."
        )
