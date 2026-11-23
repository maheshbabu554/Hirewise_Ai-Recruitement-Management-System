"""
Application configuration for HireWise AI.

Loads private settings from the .env file and provides
database, security, and file-upload configuration.
"""

import os
from pathlib import Path
from urllib.parse import quote_plus

import certifi
from dotenv import load_dotenv


# Project root directory
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables
load_dotenv(BASE_DIR / ".env")


class Config:
    """Store the common Flask application configuration."""

    # Flask security key
    SECRET_KEY = os.getenv("SECRET_KEY")

    if not SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY is missing. Please configure it in your .env file."
        )

    # MySQL-compatible TiDB connection settings
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = quote_plus(os.getenv("DB_PASSWORD", ""))
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_NAME = os.getenv("DB_NAME", "hirewise_ai")

    # Enable TLS for remote database connections
    DB_USE_TLS = (
        os.getenv("DB_USE_TLS", "false").strip().lower()
        in {"1", "true", "yes", "on"}
    )

    # SQLAlchemy database connection URI
    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        f"?charset=utf8mb4"
    )

    # SQLAlchemy settings
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Database connection pooling
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    # TiDB TLS certificate verification
    if DB_USE_TLS:
        SQLALCHEMY_ENGINE_OPTIONS["connect_args"] = {
            "ssl_ca": certifi.where(),
            "ssl_verify_cert": True,
            "ssl_verify_identity": True,
        }

    # Private resume storage
    UPLOAD_FOLDER = str(BASE_DIR / "uploads")

    # SMTP configuration
    MAIL_SERVER = os.getenv("MAIL_SERVER")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USERNAME = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = (
        os.getenv("MAIL_DEFAULT_SENDER") or MAIL_USERNAME
    )
    MAIL_USE_TLS = (
        os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    )
    MAIL_USE_SSL = (
        os.getenv("MAIL_USE_SSL", "false").lower() == "true"
    )

    # Default upload limit; app.py overrides this for bulk uploads
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024