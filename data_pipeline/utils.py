import unicodedata

def normalize_string(s):
    """Normalize a string: lowercase, strip accents, remove punctuation and extra spaces."""
    if not s:
        return ""
    # Strip accents / diacritics
    nfkd_form = unicodedata.normalize('NFKD', s)
    s_clean = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
    # Convert to lowercase and strip punctuation/extra spaces
    s_clean = s_clean.lower().strip()
    # Replace common abbreviations or marks
    for char in [".", "-", ",", "_"]:
        s_clean = s_clean.replace(char, " ")
    return " ".join(s_clean.split())
