"""Gmail API constants, thresholds, and compiled regex objects."""

import re

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# Maximum total attachment size (25MB - Gmail's practical limit)
_MAX_TOTAL_ATTACHMENT_SIZE = 25 * 1024 * 1024

# Drive API base URL (for fetching file metadata and content)
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"

# URLs shorter than this threshold are kept as-is (replacement saves little)
_URL_REPLACEMENT_MIN_LENGTH = 50

# Non-visible/zero-width Unicode characters to strip from email markdown.
# These are injected by email clients (especially Gmail) for preview text
# padding, bidirectional control, layout hints, etc. They are pure noise
# in plain-text/markdown output and waste tokens.
_INVISIBLE_UNICODE_CODEPOINTS = frozenset((
    0x200C,  # Zero Width Non-Joiner
    0x200B,  # Zero Width Space
    0x200D,  # Zero Width Joiner
    0x200E,  # Left-to-Right Mark
    0x200F,  # Right-to-Left Mark
    0x034F,  # Combining Grapheme Joiner
    0x00AD,  # Soft Hyphen
    0xFEFF,  # Zero Width No-Break Space (BOM)
    0x2060,  # Word Joiner
    0x2061,  # Function Application
    0x2062,  # Invisible Times
    0x2063,  # Invisible Separator
    0x2064,  # Invisible Plus
    0x180E,  # Mongolian Vowel Separator
    0xFFF9,  # Interlinear Annotation Anchor
    0xFFFA,  # Interlinear Annotation Separator
    0xFFFB,  # Interlinear Annotation Terminator
))

# Pre-built translation table: maps each invisible codepoint to None (deletion).
_INVISIBLE_CHAR_TRANSLATION_TABLE = str.maketrans(
    {cp: None for cp in _INVISIBLE_UNICODE_CODEPOINTS}
)

# Regex to collapse 3+ consecutive newlines (with optional interstitial
# whitespace) down to exactly 2 newlines (one blank line).
_COLLAPSE_NEWLINES_RE = re.compile(r'\n(?:[ \t]*\n){2,}')

# Regex to strip trailing horizontal whitespace from each line.
_TRAILING_WHITESPACE_RE = re.compile(r'[ \t]+$', re.MULTILINE)

# Subject prefix for self-emails
_QUEST_SUBJECT_PREFIX = "[Quest] "
