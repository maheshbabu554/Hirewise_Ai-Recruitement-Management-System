
"""
Secure one-time candidate document upload routes for HireWise AI.

Candidates receive a secure link after selection and submit
all applicable documents in one submission.

Uploaded documents are stored privately in TiDB.
"""

import hashlib
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    render_template,
    request,
)

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from extensions import db

from models import (
    Candidate,
    DocumentUploadLink,
    DocumentSubmission,
    CandidateDocument,
    AuditLog,
)

from services.file_service import create_stored_file


document_upload_bp = Blueprint(
    "document_upload",
    __name__,
    url_prefix="/candidate/documents",
)


# =========================================================
# 1. DOCUMENT CONFIGURATION
# =========================================================

MAX_FILE_SIZE = 5 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
}

DOCUMENT_FIELDS = {
    "aadhaar": {
        "category": "Identity",
        "document_type": "Aadhaar Card",
        "required": True,
    },
    "pan": {
        "category": "Identity",
        "document_type": "PAN Card",
        "required": True,
    },
    "passport": {
        "category": "Identity",
        "document_type": "Passport",
        "required": False,
    },
    "voter_id": {
        "category": "Identity",
        "document_type": "Voter ID",
        "required": False,
    },
    "driving_license": {
        "category": "Identity",
        "document_type": "Driving License",
        "required": False,
    },
    "photograph": {
        "category": "Personal",
        "document_type": "Passport-size Photograph",
        "required": True,
    },
    "tenth_certificate": {
        "category": "Education",
        "document_type": "10th Certificate",
        "required": True,
    },
    "twelfth_certificate": {
        "category": "Education",
        "document_type": "12th Certificate",
        "required": True,
    },
    "ug_certificate": {
        "category": "Education",
        "document_type": "UG Certificate",
        "required": True,
    },
    "pg_certificate": {
        "category": "Education",
        "document_type": "PG Certificate",
        "required": False,
    },
    "offer_letter": {
        "category": "Employment",
        "document_type": "Previous Offer Letter",
        "required": False,
    },
    "appointment_letter": {
        "category": "Employment",
        "document_type": "Appointment Letter",
        "required": False,
    },
    "relieving_letter": {
        "category": "Employment",
        "document_type": "Relieving Letter",
        "required": False,
    },
    "experience_certificate": {
        "category": "Employment",
        "document_type": "Experience Certificate",
        "required": False,
    },
    "salary_slips": {
        "category": "Employment",
        "document_type": "Salary Slips",
        "required": False,
    },
    "form16": {
        "category": "Employment",
        "document_type": "Form 16",
        "required": False,
    },
    "pf_document": {
        "category": "Employment",
        "document_type": "Provident Fund Document",
        "required": False,
    },
    "bank_passbook": {
        "category": "Bank",
        "document_type": "Bank Passbook Copy",
        "required": True,
    },
}


IDENTITY_ALTERNATIVES = (
    "passport",
    "voter_id",
    "driving_license",
)


MIME_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


# =========================================================
# 2. TOKEN HELPERS
# =========================================================

def hash_upload_token(token):
    """Return a SHA-256 hash of the original upload token."""

    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def get_valid_upload_link(token):
    """Find and validate a candidate's upload link."""

    if not token or len(token) > 200:
        abort(404)

    token_hash = hash_upload_token(token)

    upload_link = db.session.scalar(
        select(DocumentUploadLink).where(
            DocumentUploadLink.token_hash == token_hash
        )
    )

    if upload_link is None:
        abort(404)

    now = datetime.utcnow()

    if upload_link.used_at is not None:
        abort(410)

    if upload_link.revoked_at is not None:
        abort(410)

    if upload_link.expires_at is None:
        abort(410)

    if upload_link.expires_at <= now:
        abort(410)

    candidate = db.session.get(
        Candidate,
        upload_link.candidate_id,
    )

    if candidate is None:
        abort(404)

    if (candidate.status or "").lower() != "selected":
        abort(403)

    # Check the database directly instead of relying only
    # on a potentially cached SQLAlchemy relationship.
    existing_submission_id = db.session.scalar(
        select(DocumentSubmission.id)
        .where(
            DocumentSubmission.candidate_id == candidate.id
        )
        .limit(1)
    )

    if existing_submission_id is not None:
        abort(410)

    return upload_link, candidate


# =========================================================
# 3. FILE VALIDATION
# =========================================================

def validate_file_signature(file, extension):
    """
    Check basic file signatures.

    This is a basic format check, not a complete malware scan
    or guarantee that the file is safe to open.
    """

    file.stream.seek(0)

    header = file.stream.read(8)

    file.stream.seek(0)

    if extension == ".pdf":
        valid = header.startswith(b"%PDF-")

    elif extension in (".jpg", ".jpeg"):
        valid = header.startswith(b"\xff\xd8\xff")

    elif extension == ".png":
        valid = header.startswith(
            b"\x89PNG\r\n\x1a\n"
        )

    else:
        valid = False

    if not valid:
        raise ValueError(
            "The uploaded file content does not match its extension."
        )


def validate_document(file):
    """Validate filename, extension, size and file signature."""

    if not file or not file.filename:
        raise ValueError("Please select a document.")

    filename = secure_filename(file.filename)

    if not filename:
        raise ValueError("Invalid filename.")

    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(
            "Only PDF, JPG, JPEG and PNG files are allowed."
        )

    # Keep the filename within the database column limit.
    suffix = extension
    stem = Path(filename).stem
    filename = stem[:255 - len(suffix)] + suffix

    file.stream.seek(0, 2)

    file_size = file.stream.tell()

    file.stream.seek(0)

    if file_size <= 0:
        raise ValueError("Empty files are not allowed.")

    if file_size > MAX_FILE_SIZE:
        raise ValueError(
            "Each document must be 5 MB or smaller."
        )

    validate_file_signature(file, extension)

    return filename, extension, file_size


# =========================================================
# 4. PUBLIC ONE-TIME UPLOAD PORTAL
# =========================================================

@document_upload_bp.route(
    "/submit/<token>",
    methods=["GET", "POST"],
)
def submit_documents(token):
    """
    Allow a selected candidate to submit documents once.

    All uploaded file binaries, document records,
    submission metadata and audit records are committed
    in one database transaction.
    """

    upload_link, candidate = get_valid_upload_link(token)

    if request.method == "GET":

        return render_template(
            "candidate/document_upload.html",
            candidate=candidate,
            token=token,
            document_fields=DOCUMENT_FIELDS,
        )

    # -----------------------------------------------------
    # CHECK IDENTITY ALTERNATIVE
    # -----------------------------------------------------

    has_identity_alternative = any(
        request.files.get(field)
        and request.files[field].filename
        for field in IDENTITY_ALTERNATIVES
    )

    if not has_identity_alternative:

        flash(
            "Please upload at least one: Passport, Voter ID "
            "or Driving License.",
            "danger",
        )

        return render_template(
            "candidate/document_upload.html",
            candidate=candidate,
            token=token,
            document_fields=DOCUMENT_FIELDS,
        ), 400

    # -----------------------------------------------------
    # VALIDATE ALL DOCUMENTS
    # -----------------------------------------------------

    prepared_documents = []

    try:

        for field_name, settings in DOCUMENT_FIELDS.items():

            uploaded_file = request.files.get(field_name)

            if not uploaded_file or not uploaded_file.filename:

                if settings["required"]:
                    raise ValueError(
                        f"Please upload {settings['document_type']}."
                    )

                continue

            filename, extension, file_size = validate_document(
                uploaded_file
            )

            prepared_documents.append({
                "file": uploaded_file,
                "filename": filename,
                "extension": extension,
                "file_size": file_size,
                "category": settings["category"],
                "document_type": settings["document_type"],
            })

    except ValueError as error:

        flash(str(error), "danger")

        return render_template(
            "candidate/document_upload.html",
            candidate=candidate,
            token=token,
            document_fields=DOCUMENT_FIELDS,
        ), 400

    if not prepared_documents:

        flash(
            "Please upload the required documents.",
            "danger",
        )

        return render_template(
            "candidate/document_upload.html",
            candidate=candidate,
            token=token,
            document_fields=DOCUMENT_FIELDS,
        ), 400

    # -----------------------------------------------------
    # SAVE FILES AND DATABASE RECORDS
    # -----------------------------------------------------

    try:

        # Lock and refresh the upload link row.
        # populate_existing ensures the ORM refreshes previously
        # loaded values after the database lock is acquired.
        locked_link = db.session.scalar(
            select(DocumentUploadLink)
            .where(
                DocumentUploadLink.id == upload_link.id
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        if locked_link is None:
            abort(404)

        now = datetime.utcnow()

        # Recheck candidate status using a fresh database query.
        current_candidate_status = db.session.scalar(
            select(Candidate.status).where(
                Candidate.id == candidate.id
            )
        )

        # Recheck existing submission directly in the database.
        existing_submission_id = db.session.scalar(
            select(DocumentSubmission.id)
            .where(
                DocumentSubmission.candidate_id == candidate.id
            )
            .limit(1)
        )

        # Recheck all one-time submission conditions.
        if (
            locked_link.used_at is not None
            or locked_link.revoked_at is not None
            or locked_link.expires_at is None
            or locked_link.expires_at <= now
            or existing_submission_id is not None
            or (current_candidate_status or "").lower() != "selected"
        ):
            abort(410)

        document_records = []

        # -------------------------------------------------
        # STORE DOCUMENT BINARIES IN TIDB
        # -------------------------------------------------

        for item in prepared_documents:

            # Create the database-backed file record.
            stored_file = create_stored_file(
                item["file"],
                original_filename=item["filename"],
                mime_type=MIME_TYPES[item["extension"]],
            )

            db.session.add(stored_file)

            # Generate the StoredFile ID.
            db.session.flush()

            # Create candidate document metadata.
            document = CandidateDocument(
                candidate_id=candidate.id,
                category=item["category"],
                document_type=item["document_type"],
                original_filename=item["filename"],
                file_path=None,
                stored_file_id=stored_file.id,
                mime_type=stored_file.mime_type,
                file_size=stored_file.file_size,
                verification_status="Pending Verification",
            )

            document_records.append(document)

        # -------------------------------------------------
        # CREATE ONE-TIME DOCUMENT SUBMISSION
        # -------------------------------------------------

        submission = DocumentSubmission(
            candidate_id=candidate.id,
            status="Submitted",
            submitted_at=now,
        )

        db.session.add(submission)

        # Generate the submission ID before creating the audit.
        db.session.flush()

        # Consume the upload link.
        locked_link.used_at = now

        # Add all document records.
        db.session.add_all(document_records)

        # -------------------------------------------------
        # CREATE AUDIT RECORD
        # -------------------------------------------------

        audit = AuditLog(
            user_id=None,
            action="Candidate documents submitted",
            entity_type="Candidate",
            entity_id=candidate.id,
            details_json={
                "submission_id": submission.id,
                "document_count": len(document_records),
                "storage_location": "TiDB",
            },
        )

        db.session.add(audit)

        # -------------------------------------------------
        # COMMIT EVERYTHING TOGETHER
        # -------------------------------------------------

        db.session.commit()

    except IntegrityError:

        # Roll back the document binaries, metadata,
        # submission and consumed-link change together.
        db.session.rollback()

        flash(
            "A document submission has already been completed. "
            "Please contact your recruiter if you need assistance.",
            "danger",
        )

        return render_template(
            "candidate/document_upload.html",
            candidate=candidate,
            token=token,
            document_fields=DOCUMENT_FIELDS,
        ), 409

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "Candidate document submission failed for candidate ID %s.",
            candidate.id,
        )

        raise

    # -----------------------------------------------------
    # SUCCESS PAGE
    # -----------------------------------------------------

    return render_template(
        "candidate/document_upload_success.html",
        candidate=candidate,
    )
