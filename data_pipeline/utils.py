import unicodedata
import os

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

def load_dotenv():
    """Load .env file manually from the project root directory, stripping quotes and whitespace."""
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(root_dir, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        # Strip whitespace and quotation marks
                        v = v.strip().strip('"\'').strip()
                        os.environ[k] = v
        except Exception as e:
            print(f"[WARNING] Failed to load .env file: {e}")

