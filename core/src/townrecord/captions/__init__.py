"""Caption parsing for the capture pipeline (spec 6.2, 8.3).

srv3 and WebVTT caption files become timed segments. Nothing here touches the
network, the database or the filesystem.
"""

from townrecord.captions.parse import (
    CaptionParseError,
    Segment,
    Word,
    parse_srv3,
    parse_vtt,
)

__all__ = [
    "CaptionParseError",
    "Segment",
    "Word",
    "parse_srv3",
    "parse_vtt",
]
