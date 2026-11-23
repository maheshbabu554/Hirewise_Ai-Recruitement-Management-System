
"""
Email service for HireWise AI.

Handles:
- Shortlist notification emails.
- Interview schedule emails.
- Candidate selection emails.
- Document upload link emails.
- Candidate rejection emails.
- PDF offer letter generation and delivery.
- Onboarding emails.
- Document verification notifications.
- Login OTP emails.

Email providers:
- Brevo HTTPS API (recommended for Render).
- SMTP (optional for environments that allow SMTP).

Company name used in candidate-facing documents: Mitraz.
"""

import base64
import json
import logging
import os
import smtplib
import urllib.error
import urllib.request

from email.message import EmailMessage
from email.utils import formataddr, getaddresses, parseaddr
from io import BytesIO
from xml.sax.saxutils import escape

from flask import current_app

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


logger = logging.getLogger(__name__)

COMPANY_NAME = "Mitraz"

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
EMAIL_TIMEOUT = 20


# =========================================================
# 1. GENERAL HELPERS
# =========================================================

def get_setting(name, default=None):
    """
    Read a setting from Flask configuration first,
    then fall back to environment variables.
    """

    value = current_app.config.get(name)

    if value is None or value == "":
        value = os.getenv(name, default)

    return value


def get_bool_setting(name, default=False):
    """Read a boolean setting safely."""

    value = get_setting(name, default)

    if isinstance(value, bool):
        return value

    if value is None:
        return default

    return str(value).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def safe_text(value):
    """Convert values to safe text for ReportLab Paragraphs."""

    return escape(str(value or ""))


def validate_recipient(recipient):
    """Validate that a recipient email address is provided."""

    if not recipient or not isinstance(recipient, str):
        raise ValueError("Candidate email address is missing.")

    recipient = recipient.strip()

    name, address = parseaddr(recipient)

    if (
        not address
        or "@" not in address
        or address.startswith("@")
        or address.endswith("@")
        or any(character.isspace() for character in address)
    ):
        raise ValueError(
            "A valid candidate email address is required."
        )

    return address


def get_email_addresses(header_value):
    """Extract valid email addresses from an email header."""

    if not header_value:
        return []

    addresses = []

    for name, address in getaddresses([str(header_value)]):
        address = address.strip()

        if address and "@" in address:
            addresses.append({
                "email": address,
                **({"name": name} if name else {}),
            })

    return addresses


def get_sender_details():
    """Get the configured sender name and email address."""

    sender = (
        get_setting("MAIL_DEFAULT_SENDER")
        or get_setting("MAIL_USERNAME")
    )

    if not sender:
        raise ValueError(
            "Email sender is not configured. "
            "Set MAIL_DEFAULT_SENDER."
        )

    sender_name, sender_email = parseaddr(str(sender))

    if (
        not sender_email
        or "@" not in sender_email
        or sender_email.startswith("@")
        or sender_email.endswith("@")
    ):
        raise ValueError(
            "MAIL_DEFAULT_SENDER must contain a valid email address."
        )

    return {
        "name": sender_name or COMPANY_NAME,
        "email": sender_email,
    }


def send_email(recipient, subject, body):
    """
    Send a plain-text email.

    Retained for compatibility with existing routes.
    """

    recipient = validate_recipient(recipient)

    if not subject:
        raise ValueError("Email subject is required.")

    if not body:
        raise ValueError("Email body is required.")

    message = EmailMessage()
    message["Subject"] = str(subject)
    message["To"] = recipient
    message.set_content(str(body))

    send_message(message)

    return True


# =========================================================
# 2. EMAIL CONTENT HELPERS
# =========================================================

def get_message_content(message, content_type):
    """
    Extract plain-text or HTML content from an EmailMessage.

    Returns None when the requested content type is unavailable.
    """

    try:
        part = message.get_body(
            preferencelist=(content_type,)
        )

        if part is None:
            if message.get_content_type() == content_type:
                part = message
            else:
                return None

        content = part.get_content()

        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")

        return str(content)

    except (AttributeError, LookupError, TypeError, ValueError):
        logger.exception(
            "Could not extract %s email content.",
            content_type,
        )
        return None


def get_brevo_attachments(message):
    """Convert email attachments into Brevo API attachment objects."""

    attachments = []

    for part in message.iter_attachments():
        filename = part.get_filename()

        if not filename:
            filename = "attachment"

        content = part.get_payload(decode=True)

        if content is None:
            content = part.get_content()

        if isinstance(content, str):
            content = content.encode("utf-8")

        if not isinstance(content, bytes):
            raise ValueError(
                "Unsupported email attachment content."
            )

        attachments.append({
            "name": filename,
            "content": base64.b64encode(content).decode("ascii"),
        })

    return attachments


# =========================================================
# 3. BREVO EMAIL SENDER
# =========================================================

def send_brevo_message(message):
    """
    Send an EmailMessage using the Brevo HTTPS API.

    Supports:
    - Plain-text email content.
    - HTML email content.
    - Multiple recipients.
    - CC and BCC.
    - File attachments, including PDF offer letters.

    The Brevo API key is read from the environment and is
    never included in logs.
    """

    api_key = get_setting("BREVO_API_KEY")

    if not api_key:
        raise ValueError(
            "BREVO_API_KEY is missing. "
            "Configure it in the deployment environment."
        )

    sender = get_sender_details()

    recipients = get_email_addresses(message.get("To"))

    if not recipients:
        raise ValueError(
            "At least one valid email recipient is required."
        )

    subject = str(message.get("Subject", "")).strip()

    if not subject:
        raise ValueError("Email subject is required.")

    payload = {
        "sender": sender,
        "to": recipients,
        "subject": subject,
    }

    cc_recipients = get_email_addresses(message.get("Cc"))
    bcc_recipients = get_email_addresses(message.get("Bcc"))

    if cc_recipients:
        payload["cc"] = cc_recipients

    if bcc_recipients:
        payload["bcc"] = bcc_recipients

    plain_content = get_message_content(
        message,
        "text/plain",
    )

    html_content = get_message_content(
        message,
        "text/html",
    )

    if plain_content:
        payload["textContent"] = plain_content

    if html_content:
        payload["htmlContent"] = html_content

    if not plain_content and not html_content:
        raise ValueError(
            "Email message does not contain valid text or HTML content."
        )

    attachments = get_brevo_attachments(message)

    if attachments:
        payload["attachment"] = attachments

    request_data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        BREVO_API_URL,
        data=request_data,
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=EMAIL_TIMEOUT,
        ) as response:

            status_code = response.getcode()
            response_body = response.read().decode(
                "utf-8",
                errors="replace",
            )

            if status_code not in (200, 201, 202):
                raise RuntimeError(
                    "Brevo returned an unexpected HTTP status: "
                    "{}".format(status_code)
                )

            try:
                response_data = json.loads(response_body)
            except (json.JSONDecodeError, TypeError):
                response_data = {}

            logger.info(
                "Email successfully submitted through Brevo. "
                "HTTP status: %s",
                status_code,
            )

            return response_data

    except urllib.error.HTTPError as error:
        # Do not log request headers or the API key.
        try:
            error_body = error.read().decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            error_body = "No error response body available."

        logger.error(
            "Brevo email API request failed. HTTP status: %s. "
            "Response: %s",
            error.code,
            error_body[:1000],
        )

        raise RuntimeError(
            "Brevo email delivery failed. "
            "HTTP status: {}.".format(error.code)
        ) from error

    except urllib.error.URLError as error:
        logger.error(
            "Brevo connection failed: %s",
            getattr(error, "reason", "Unknown connection error"),
        )

        raise RuntimeError(
            "Could not connect to the Brevo email service."
        ) from error

    except (TimeoutError, OSError):
        logger.exception(
            "Brevo email request timed out or encountered a network error."
        )
        raise

    except Exception:
        logger.exception(
            "Unexpected error while sending email through Brevo."
        )
        raise


# =========================================================
# 4. SMTP EMAIL SENDER
# =========================================================

def send_smtp_message(message):
    """
    Send an email using configured SMTP settings.

    This option is retained for environments that permit
    outbound SMTP connections.

    Render Free commonly blocks outbound SMTP ports, so
    Brevo is recommended for the deployed application.
    """

    mail_server = get_setting("MAIL_SERVER")
    mail_port = get_setting("MAIL_PORT", 587)
    mail_username = get_setting("MAIL_USERNAME")
    mail_password = get_setting("MAIL_PASSWORD")

    sender = (
        get_setting("MAIL_DEFAULT_SENDER")
        or mail_username
    )

    recipient = message.get("To")

    if not all([
        mail_server,
        mail_port,
        sender,
        recipient,
    ]):
        raise ValueError(
            "Email configuration is incomplete. "
            "Check MAIL_SERVER, MAIL_PORT, "
            "MAIL_DEFAULT_SENDER and recipient."
        )

    try:
        mail_port = int(mail_port)
    except (TypeError, ValueError):
        raise ValueError(
            "MAIL_PORT must be a valid integer."
        )

    use_ssl = get_bool_setting("MAIL_USE_SSL", False)
    use_tls = get_bool_setting("MAIL_USE_TLS", True)

    if use_ssl and use_tls:
        raise ValueError(
            "Invalid email configuration: enable SSL or TLS, not both."
        )

    message["From"] = sender

    smtp = None

    try:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(
                mail_server,
                mail_port,
                timeout=EMAIL_TIMEOUT,
            )

        else:
            smtp = smtplib.SMTP(
                mail_server,
                mail_port,
                timeout=EMAIL_TIMEOUT,
            )

            smtp.ehlo()

            if use_tls:
                smtp.starttls()
                smtp.ehlo()

        if mail_username and mail_password:
            smtp.login(
                mail_username,
                mail_password,
            )

        refused = smtp.send_message(message)

        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)

        logger.info(
            "Email successfully submitted to SMTP server."
        )

    except (smtplib.SMTPException, OSError):
        logger.exception(
            "SMTP email sending failed."
        )
        raise

    finally:
        if smtp is not None:
            try:
                smtp.quit()

            except (smtplib.SMTPException, OSError):
                try:
                    smtp.close()
                except OSError:
                    pass


# =========================================================
# 5. EMAIL PROVIDER DISPATCHER
# =========================================================

def send_message(message):
    """
    Send an email using the configured provider.

    EMAIL_PROVIDER=brevo  -> Brevo HTTPS API
    EMAIL_PROVIDER=smtp   -> SMTP

    Brevo failures are raised instead of silently falling back
    to SMTP. This avoids long timeouts on Render, where SMTP
    connections may be blocked.
    """

    provider = str(
        get_setting("EMAIL_PROVIDER", "brevo")
    ).strip().lower()

    if provider == "brevo":
        return send_brevo_message(message)

    if provider == "smtp":
        return send_smtp_message(message)

    raise ValueError(
        "Unsupported EMAIL_PROVIDER '{}'. "
        "Use 'brevo' or 'smtp'.".format(provider)
    )


# =========================================================
# 6. SHORTLIST EMAIL
# =========================================================

def send_shortlist_email(
    candidate_name,
    recipient,
    job_title,
):
    """Notify a candidate that they have been shortlisted."""

    recipient = validate_recipient(recipient)

    if not candidate_name or not job_title:
        raise ValueError(
            "Candidate name and job title are required."
        )

    subject = (
        "Congratulations! You Have Been Shortlisted - {}".format(
            job_title
        )
    )

    body = f"""
Dear {candidate_name},

Congratulations!

We are pleased to inform you that you have been shortlisted
for the position of {job_title} at {COMPANY_NAME}.

Our recruitment team will contact you regarding the next
steps in the recruitment process.

Please keep checking your email for further updates.

Best Regards,
Recruitment Team
{COMPANY_NAME}
"""

    send_email(
        recipient=recipient,
        subject=subject,
        body=body.strip(),
    )

    return True


# =========================================================
# 7. INTERVIEW SCHEDULE EMAIL
# =========================================================

def send_interview_schedule_email(
    candidate_name,
    recipient,
    job_title,
    interview_date,
    interview_time,
    interview_mode,
    meeting_link=None,
    interview_location=None,
    additional_instructions="",
):
    """Send interview schedule details to a candidate."""

    recipient = validate_recipient(recipient)

    if not all([
        candidate_name,
        job_title,
        interview_date,
        interview_time,
        interview_mode,
    ]):
        raise ValueError(
            "Required interview schedule details are missing."
        )

    if interview_mode.lower() == "online" and not meeting_link:
        raise ValueError(
            "A meeting link is required for an online interview."
        )

    if interview_mode.lower() == "offline" and not interview_location:
        raise ValueError(
            "An interview location is required for an offline interview."
        )

    location_details = ""

    if interview_mode.lower() == "online":
        location_details = (
            "Meeting Link: {}\n".format(meeting_link)
        )

    elif interview_mode.lower() == "offline":
        location_details = (
            "Interview Location: {}\n".format(interview_location)
        )

    instructions = (
        additional_instructions
        or "Please join or arrive a few minutes before the scheduled time."
    )

    subject = "Interview Scheduled - {}".format(job_title)

    body = f"""
Dear {candidate_name},

Your interview has been scheduled.

Interview Details:

Company: {COMPANY_NAME}
Position: {job_title}
Date: {interview_date}
Time: {interview_time}
Mode: {interview_mode}

{location_details}
Instructions:
{instructions}

Please contact our recruitment team if you have any questions.

Best Regards,
Recruitment Team
{COMPANY_NAME}
"""

    send_email(
        recipient=recipient,
        subject=subject,
        body=body.strip(),
    )

    return True


# =========================================================
# 8. SELECTION EMAIL WITH DOCUMENT UPLOAD LINK
# =========================================================

def send_selection_email(
    candidate_name,
    recipient,
    job_title,
    document_upload_link,
    submission_deadline=None,
):
    """
    Notify a selected candidate and provide the document upload link.

    The upload link must be generated by the document service.
    This function only sends the supplied link.
    """

    recipient = validate_recipient(recipient)

    if not all([
        candidate_name,
        job_title,
        document_upload_link,
    ]):
        raise ValueError(
            "Candidate name, job title and document upload link "
            "are required."
        )

    deadline_message = ""

    if submission_deadline:
        deadline_message = (
            "\nDocument submission deadline: {}\n".format(
                submission_deadline
            )
        )

    subject = (
        "Congratulations! You Have Been Selected - {}".format(
            job_title
        )
    )

    body = f"""
Dear {candidate_name},

Congratulations!

We are pleased to inform you that you have been selected
for the position of {job_title} at {COMPANY_NAME}.

DOCUMENT SUBMISSION

Please use the secure link below to upload your required documents.

Document Upload Link:
{document_upload_link}
{deadline_message}
Important Instructions:

1. Upload all applicable required documents together.
2. Check that your documents are clear and readable.
3. Review your uploaded documents before submitting.
4. Submit the form only after checking all the information.

IMPORTANT:
Document submission is allowed only once.
After successful submission, you cannot independently
edit or submit the form again.

Our recruitment team will manually verify your documents.

Best Regards,
Recruitment Team
{COMPANY_NAME}
"""

    send_email(
        recipient=recipient,
        subject=subject,
        body=body.strip(),
    )

    return True


# =========================================================
# 9. REJECTION EMAIL
# =========================================================

def send_rejection_email(
    candidate_name,
    recipient,
    job_title,
    additional_message="",
):
    """Send a professional candidate rejection email."""

    recipient = validate_recipient(recipient)

    if not candidate_name or not job_title:
        raise ValueError(
            "Candidate name and job title are required."
        )

    extra_message = ""

    if additional_message:
        extra_message = "\n{}\n".format(additional_message)

    subject = "Application Update - {}".format(job_title)

    body = f"""
Dear {candidate_name},

Thank you for your interest in the position of {job_title}
at {COMPANY_NAME}.

After careful consideration, we regret to inform you that
we will not be proceeding with your application at this time.

We sincerely appreciate the time and effort you invested
in our recruitment process.

{extra_message}
We wish you success in your future career.

Best Regards,
Recruitment Team
{COMPANY_NAME}
"""

    send_email(
        recipient=recipient,
        subject=subject,
        body=body.strip(),
    )

    return True


# =========================================================
# 10. ONBOARDING EMAIL
# =========================================================

def send_onboarding_email(
    candidate_name,
    recipient,
    job_title,
    joining_date,
    reporting_time=None,
    reporting_location=None,
    additional_instructions="",
):
    """Send onboarding instructions after the offer letter is sent."""

    recipient = validate_recipient(recipient)

    if not all([
        candidate_name,
        job_title,
        joining_date,
    ]):
        raise ValueError(
            "Candidate name, job title and joining date are required."
        )

    reporting_details = ""

    if reporting_time:
        reporting_details += (
            "Reporting Time: {}\n".format(reporting_time)
        )

    if reporting_location:
        reporting_details += (
            "Reporting Location: {}\n".format(reporting_location)
        )

    instructions = (
        additional_instructions
        or "Please carry your original documents for verification "
           "if requested by the recruitment team."
    )

    subject = "Onboarding Instructions - {}".format(job_title)

    body = f"""
Dear {candidate_name},

Congratulations once again!

We are pleased to welcome you to {COMPANY_NAME}.

Your onboarding details are provided below.

Company: {COMPANY_NAME}
Position: {job_title}
Joining Date: {joining_date}

{reporting_details}
Additional Instructions:
{instructions}

Please contact our recruitment team if you have any questions.

We look forward to welcoming you to our team.

Best Regards,
HR / Recruitment Team
{COMPANY_NAME}
"""

    send_email(
        recipient=recipient,
        subject=subject,
        body=body.strip(),
    )

    return True


# =========================================================
# 11. OFFER LETTER PDF GENERATION
# =========================================================

def create_offer_pdf(
    candidate_name,
    job_title,
    salary,
    joining_date,
    additional_message="",
):
    """Generate a professional PDF offer letter in memory."""

    pdf_buffer = BytesIO()

    document = SimpleDocTemplate(
        pdf_buffer,
        pagesize=A4,
        rightMargin=22 * mm,
        leftMargin=22 * mm,
        topMargin=22 * mm,
        bottomMargin=22 * mm,
    )

    styles = getSampleStyleSheet()

    company_style = ParagraphStyle(
        "CompanyHeader",
        parent=styles["Title"],
        fontSize=24,
        leading=30,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#17365D"),
        spaceAfter=8,
    )

    heading_style = ParagraphStyle(
        "OfferHeading",
        parent=styles["Heading1"],
        fontSize=17,
        leading=22,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#198754"),
        spaceAfter=20,
    )

    body_style = ParagraphStyle(
        "OfferBody",
        parent=styles["BodyText"],
        fontSize=10.5,
        leading=17,
        spaceAfter=12,
    )

    story = [
        Paragraph(COMPANY_NAME, company_style),
        Paragraph("EMPLOYMENT OFFER LETTER", heading_style),
        Paragraph(
            "Dear {}, ".format(safe_text(candidate_name)),
            body_style,
        ),
        Paragraph(
            "Congratulations! We are pleased to offer you "
            "the position of <b>{}</b> at {}.".format(
                safe_text(job_title),
                COMPANY_NAME,
            ),
            body_style,
        ),
        Spacer(1, 10),
        Paragraph("Offer Details", styles["Heading2"]),
    ]

    offer_details = [
        [
            Paragraph("<b>Description</b>", body_style),
            Paragraph("<b>Details</b>", body_style),
        ],
        ["Company", COMPANY_NAME],
        ["Candidate Name", safe_text(candidate_name)],
        ["Position", safe_text(job_title)],
        ["Annual Salary / CTC", safe_text(salary)],
        ["Expected Joining Date", safe_text(joining_date)],
    ]

    details_table = Table(
        offer_details,
        colWidths=[65 * mm, 95 * mm],
        repeatRows=1,
    )

    details_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0),
             colors.HexColor("#17365D")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 1), (-1, -1),
             colors.HexColor("#F5F7FA")),
            ("PADDING", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )

    story.extend([
        details_table,
        Spacer(1, 20),
        Paragraph("Additional Information", styles["Heading2"]),
    ])

    message = additional_message or "No additional information."

    safe_message = safe_text(message).replace("\n", "<br/>")

    story.append(
        Paragraph(safe_message, body_style)
    )

    story.extend([
        Spacer(1, 20),
        Paragraph(
            "We look forward to welcoming you to our team. "
            "Please contact our recruitment team if you have "
            "any questions.",
            body_style,
        ),
        Spacer(1, 30),
        Paragraph(
            "Best Regards,<br/><br/>"
            "Authorized Signatory<br/>"
            "<b>{}</b>".format(COMPANY_NAME),
            body_style,
        ),
    ])

    document.build(story)

    pdf_buffer.seek(0)

    return pdf_buffer.getvalue()


# =========================================================
# 12. OFFER LETTER EMAIL
# =========================================================

def send_offer_email(
    candidate_name,
    recipient,
    job_title,
    salary,
    joining_date,
    additional_message="",
):
    """Generate and email a PDF offer letter."""

    recipient = validate_recipient(recipient)

    if not all([
        candidate_name,
        job_title,
        salary,
        joining_date,
    ]):
        raise ValueError(
            "Required offer letter details are missing."
        )

    pdf_content = create_offer_pdf(
        candidate_name=candidate_name,
        job_title=job_title,
        salary=salary,
        joining_date=joining_date,
        additional_message=additional_message,
    )

    message = EmailMessage()

    message["Subject"] = "Job Offer - {}".format(job_title)
    message["To"] = recipient

    body = f"""
Dear {candidate_name},

Congratulations!

We are pleased to offer you the position of {job_title}
at {COMPANY_NAME}.

Your official offer letter is attached to this email as a PDF.

Please review the offer details and contact our recruitment
team if you have any questions.

We look forward to welcoming you to our team.

Best Regards,
Recruitment Team
{COMPANY_NAME}
"""

    message.set_content(body.strip())

    message.add_attachment(
        pdf_content,
        maintype="application",
        subtype="pdf",
        filename="Mitraz_Offer_Letter.pdf",
    )

    send_message(message)

    return True


# =========================================================
# 13. DOCUMENT VERIFICATION ADMIN EMAIL
# =========================================================

def send_document_verification_admin_email(
    candidate_name=None,
    recipient=None,
    job_title=None,
    document_name=None,
    decision=None,
    remarks="",
    **kwargs,
):
    """Notify the administrator about a document verification decision."""

    candidate_name = (
        candidate_name
        or kwargs.get("full_name")
        or "Unknown Candidate"
    )

    recipient = (
        recipient
        or kwargs.get("admin_email")
        or kwargs.get("recipient_email")
    )

    job_title = (
        job_title
        or kwargs.get("vacancy_title")
        or "Not specified"
    )

    document_name = (
        document_name
        or kwargs.get("original_filename")
        or kwargs.get("filename")
        or "Not specified"
    )

    decision = (
        decision
        or kwargs.get("verification_status")
        or "Not specified"
    )

    remarks = (
        remarks
        or kwargs.get("verification_notes")
        or ""
    )

    recipient = validate_recipient(recipient)

    subject = (
        "Candidate Document Verification Update - {}".format(
            candidate_name
        )
    )

    body = f"""
Hello Administrator,

A candidate document verification decision has been recorded.

Candidate Details:

Candidate Name: {candidate_name}
Position: {job_title}

Document Details:

Document Name: {document_name}
Verification Decision: {decision}
Remarks: {remarks or "No remarks provided."}

Please review the recruitment records if further action is required.

Regards,
HireWise AI
"""

    send_email(
        recipient=recipient,
        subject=subject,
        body=body.strip(),
    )

    return True


# =========================================================
# 14. LOGIN OTP EMAIL
# =========================================================

def send_login_otp_email(recipient, otp):
    """Send a login verification OTP to an admin or recruiter."""

    recipient = validate_recipient(recipient)

    otp = str(otp).strip() if otp is not None else ""

    if len(otp) != 6 or not otp.isdigit():
        raise ValueError(
            "A valid six-digit OTP is required."
        )

    subject = "HireWise AI - Login Verification OTP"

    body = f"""
Hello,

Your HireWise AI login verification code is:

{otp}

This OTP is valid for only 120 seconds (2 minutes).

Do not share this code with anyone.

If you did not attempt to log in, please ignore this email.

Regards,
HireWise AI Security Team
"""

    send_email(
        recipient=recipient,
        subject=subject,
        body=body.strip(),
    )

    return True
