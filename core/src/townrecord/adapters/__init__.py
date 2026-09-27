"""Records adapters: one per records system (spec 9)."""

from .base import (
    PACKET_LIMIT_BYTES,
    PDF_LIMIT_BYTES,
    AdapterError,
    AdapterHttpError,
    AgendaItem,
    Document,
    DocumentContent,
    DocumentTooLarge,
    HealthResult,
    Meeting,
    RecordsAdapter,
    RedirectLimitExceeded,
)
from .primegov import PrimeGovAdapter, decode_html, detect_encoding, user_agent

__all__ = [
    "PDF_LIMIT_BYTES",
    "PACKET_LIMIT_BYTES",
    "AdapterError",
    "AdapterHttpError",
    "AgendaItem",
    "Document",
    "DocumentContent",
    "DocumentTooLarge",
    "HealthResult",
    "Meeting",
    "PrimeGovAdapter",
    "RecordsAdapter",
    "RedirectLimitExceeded",
    "decode_html",
    "detect_encoding",
    "user_agent",
]
