import re

def preprocess_military_time(when: str) -> str:
    # 1. Exactly 4 digits: "1500"
    if re.fullmatch(r'([0-2][0-9][0-5][0-9])', when.strip()):
        return f"{when.strip()[:2]}:{when.strip()[2:]}"
    
    # 2. Preceded by common time words: "at 1500", "tomorrow 1500", "on friday 1500"
    # Actually, let's just match "at " or "tomorrow " or "today " or "tonight "
    when = re.sub(r'\b(at|tomorrow|today|tonight)\s+([0-2][0-9])([0-5][0-9])\b', r'\1 \2:\3', when, flags=re.IGNORECASE)
    
    # 3. Followed by "hours" or "hrs", NOT preceded by "in "
    # Python re requires fixed-width lookbehind, so we just use negative lookbehind for "in " (3 chars)
    # If there are multiple spaces, it won't match, so let's just not worry about multiple spaces or use a function.
    def replace_hours(match):
        if match.group(1).lower().endswith("in "):
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}:{match.group(3)}"
    
    # match anything before it
    when = re.sub(r'(^|.*?\s)([0-2][0-9])([0-5][0-9])\s*(?:hours|hrs)\b', replace_hours, when, flags=re.IGNORECASE)
    
    return when

cases = [
    "tomorrow 1500", "today 0800", "at 1500", "in 1500 hours", "1500 hours", "tomorrow at 1500"
]

for c in cases:
    parsed = preprocess_military_time(c)
    print(f"'{c}' -> '{parsed}'")

