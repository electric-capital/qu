"""Shared helper functions and constants for action request types."""


def _extract_first_name(full_name: str) -> str:
    """Extract the first name from a full name string.

    Splits on whitespace and returns the first token, or an empty string
    if the input is empty/blank.
    """
    parts = full_name.strip().split()
    return parts[0] if parts else ""
