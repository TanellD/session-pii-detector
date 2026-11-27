"""Russian numeric phone syntax shared by regex and model candidates."""
import re

PHONE_BODY = r"(?:\+7|8)[ -]?(?:\([0-9]{3}\)|[0-9]{3})[ -]?[0-9]{3}[ -]?[0-9]{2}[ -]?[0-9]{2}"
PHONE_RE = re.compile(PHONE_BODY)
CONTINUATION_BEFORE = re.compile(r"[0-9][ ()-]*$")
CONTINUATION_AFTER = re.compile(r"^[ ()-]*[0-9]")

def valid_phone_span(text, start, end):
    value = text[start:end]
    return (PHONE_RE.fullmatch(value) is not None
            and not CONTINUATION_BEFORE.search(text[:start])
            and not CONTINUATION_AFTER.match(text[end:]))
