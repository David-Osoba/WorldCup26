import os
import json
import sys
from data_pipeline.utils import normalize_string

def levenshtein_distance(s1, s2):
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

def jaro_similarity(s1, s2):
    if s1 == s2:
        return 1.0
    len1 = len(s1)
    len2 = len(s2)
    max_dist = max(len1, len2) // 2 - 1
    if max_dist < 0:
        max_dist = 0
    match = 0
    hash_s1 = [0] * len1
    hash_s2 = [0] * len2
    for i in range(len1):
        for j in range(max(0, i - max_dist), min(len2, i + max_dist + 1)):
            if s1[i] == s2[j] and hash_s2[j] == 0:
                hash_s1[i] = 1
                hash_s2[j] = 1
                match += 1
                break
    if match == 0:
        return 0.0
    t = 0
    point = 0
    for i in range(len1):
        if hash_s1[i]:
            while hash_s2[point] == 0:
                point += 1
            if s1[i] != s2[point]:
                t += 1
            point += 1
    t = t // 2
    return (match / len1 + match / len2 + (match - t) / match) / 3.0

def jaro_winkler_similarity(s1, s2, p=0.1):
    jaro = jaro_similarity(s1, s2)
    prefix = 0
    for i in range(min(4, len(s1), len(s2))):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break
    return jaro + prefix * p * (1.0 - jaro)

class EntityResolver:
    def __init__(self, aliases_path="config/aliases.json"):
        self.aliases_path = aliases_path
        self.load_aliases()

    def load_aliases(self):
        if os.path.exists(self.aliases_path):
            with open(self.aliases_path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {"managers": {}, "teams": {}}
            os.makedirs(os.path.dirname(self.aliases_path), exist_ok=True)
            self.save_aliases()

    def save_aliases(self):
        with open(self.aliases_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    def resolve_manager(self, name, known_managers=None):
        """Resolves manager name to canonical name."""
        return self._resolve_entity(name, "managers", known_managers)

    def resolve_team(self, name, known_teams=None):
        """Resolves team name to canonical name."""
        return self._resolve_entity(name, "teams", known_teams)

    def _resolve_entity(self, name, category, known_entities=None):
        if not name:
            return ""
        norm_name = normalize_string(name)
        
        # 1. Check direct aliases map
        category_data = self.data.get(category, {})
        for key, val in category_data.items():
            if normalize_string(key) == norm_name:
                return val
            
        # If the name is already canonical in our mapping keys/values, return it
        for alias, canonical in category_data.items():
            if norm_name == normalize_string(canonical):
                return canonical
                
        # 2. Match against list of known entities
        if not known_entities:
            # Gather unique canonical names from aliases values
            known_entities = list(set(self.data.get(category, {}).values()))
            
        if not known_entities:
            # No known targets, assume canonical
            self.data[category][norm_name] = name
            self.save_aliases()
            return name

        # 3. Fuzzy search in known entities
        best_match = None
        best_score = 0.0
        
        for candidate in known_entities:
            norm_candidate = normalize_string(candidate)
            # Calculate Jaro-Winkler score
            score = jaro_winkler_similarity(norm_name, norm_candidate)
            if score > best_score:
                best_score = score
                best_match = candidate
                
        # Threshold checks
        if best_score >= 0.96:
            # Auto-resolve and cache alias
            self.data[category][norm_name] = best_match
            self.save_aliases()
            return best_match
            
        elif best_score >= 0.70:
            # Pause and ask if terminal is interactive
            prompt_msg = f"\n[ENTITY RESOLUTION] Fuzzy match found: '{name}' -> '{best_match}' (Confidence: {best_score:.2f}). Is this correct? (y/n): "
            
            # Interactive fallback
            if sys.stdin.isatty() and os.environ.get("NON_INTERACTIVE") != "1":
                try:
                    response = input(prompt_msg).strip().lower()
                    if response in ['y', 'yes']:
                        self.data[category][norm_name] = best_match
                        self.save_aliases()
                        return best_match
                except Exception as e:
                    print(f"Failed to get user input: {e}")
            
            # Non-interactive fallback: treat as a new canonical entity to prevent incorrect fuzzy mapping
            print(f"[INFO] Non-interactive fuzzy match unconfirmed: registering '{name}' as a new canonical entity (best match was '{best_match}' with score {best_score:.2f})")
            self.data[category][norm_name] = name
            self.save_aliases()
            return name
            
        else:
            # Score is too low, treat as new canonical entity
            print(f"[INFO] New canonical entity registered: '{name}' (No matches above threshold)")
            self.data[category][norm_name] = name
            self.save_aliases()
            return name
