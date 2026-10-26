"""Static ATT discovery, not a Lua interpreter or a confirmed mechanics database.

Every pinned source file has a terminal disposition. Constructor relationships
are source claims: a collection child is never silently a guaranteed output.
Unsupported expressions and symbol references remain explicit research gaps.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from .knowledge_sources import ATT_BUILD, ATT_REVISION, ATT_STANDARD, ATT_SHARED, ATT_TREE, dependency_disposition, include_path, source_policy

PARSER_REVISION = "att-static-3"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOKENS = 2_000_000
MAX_CANDIDATES = 500_000
FIELDS = {"cost", "_cost", "providers", "provider", "qg", "qgs", "crs", "sourceQuest", "sourceQuests",
          "timeline", "u", "isDaily", "isWeekly", "isMonthly", "isYearly", "repeatable", "isRepeatable",
          "races", "classes", "r", "c", "lvl", "minReputation", "requireSkill", "skillID", "recipeID",
          "spellID", "itemID", "currencyID", "modID", "bonusID", "sym", "sharedDescription", "binding",
          "rank", "reagents", "collectible", "description", "q", "aqd", "hqd"}
ID_KINDS = {"i": "item", "item": "item", "ig": "item", "container": "item", "salvagerecipe": "recipe",
            "r": "recipe", "recipe": "recipe", "spell": "spell", "sp": "spell", "n": "npc",
            "q": "quest", "hqt": "quest", "hqa": "quest", "currency": "currency", "o": "object",
            "p": "pet", "s": "appearance", "prof": "profession", "profession": "profession", "tier": "tier"}
EXPANSIONS = {"CLASSIC": 1, "TBC": 2, "WRATH": 3, "CATA": 4, "MOP": 5, "WOD": 6,
              "LEGION": 7, "BFA": 8, "SL": 9, "DF": 10, "TWW": 11, "MID": 12, "TLT": 13}
PATH_ERAS = [("Classic", "Classic"), ("Burning Crusade", "The Burning Crusade"), ("Outland", "The Burning Crusade"),
             ("Wrath", "Wrath of the Lich King"), ("Northrend", "Wrath of the Lich King"),
             ("Cataclysm", "Cataclysm"), ("Pandaria", "Mists of Pandaria"), ("Draenor", "Warlords of Draenor"),
             ("Legion", "Legion"), ("Broken Isles", "Legion"), ("Battle for Azeroth", "Battle for Azeroth"),
             ("Kul Tiras", "Battle for Azeroth"), ("Zandalar", "Battle for Azeroth"), ("Nazjatar", "Battle for Azeroth"),
             ("Shadowlands", "Shadowlands"), ("Dragonflight", "Dragonflight"), ("Dragon Isles", "Dragonflight"),
             ("The War Within", "The War Within"), ("Khaz Algar", "The War Within"),
             ("Midnight", "Midnight"), ("Quel'Thalas", "Midnight")]
NUMBER = re.compile(r"(?:0[xX][0-9a-fA-F]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)")


class ATTError(ValueError):
    pass


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _version(value: str) -> tuple[int, ...]:
    return tuple(int(v) for v in value.split("."))


def _condition(expression: str, build: str) -> bool | None:
    """Small explicitly supported preprocessor subset; unknown is never true."""
    expression = expression.strip().upper()
    if expression == "RETAIL":
        return True
    if expression in EXPANSIONS:
        return EXPANSIONS[expression] == int(build.split(".")[0])
    if expression in {"CLASSIC", "ANYCLASSIC", "SOD", "SEASON_OF_DISCOVERY", "PTR", "BETA"}:
        return False
    for operator in (" OR ", " AND "):
        if operator in expression:
            values = [_condition(part, build) for part in expression.split(operator)]
            if operator == " OR ":
                return True if True in values else None if None in values else False
            return False if False in values else None if None in values else True
    if expression.startswith("NOT "):
        value = _condition(expression[4:], build)
        return None if value is None else not value
    match = re.fullmatch(r"(AFTER|BEFORE)\s+([A-Z_0-9.]+)", expression)
    if match:
        target = match[2]
        if target in EXPANSIONS:
            version = (EXPANSIONS[target],)
        elif re.fullmatch(r"\d+(?:\.\d+)*", target):
            version = _version(target)
        else:
            return None
        current = _version(build)
        return current >= version if match[1] == "AFTER" else current < version
    return None


def preprocess(text: str, build: str) -> tuple[str, list[dict], list[dict]]:
    """Exclude provably false lines; retain unknown branches with diagnostics."""
    stack: list[dict] = []
    lines, regions, diagnostics = [], [], []
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        directive = re.match(r"\s*--\s*#\s*(if|elseif|else|endif|endfif|end)\b\s*(.*)", line, re.I)
        if directive:
            kind, expr = directive.groups()
            kind = kind.lower()
            # Pinned ATT Program.cs ProcessInternalCommandBlock default closes
            # the current branch. These two actual corpus spellings use it.
            if kind in {"end", "endfif"}:
                kind = "endif"
            if kind == "if":
                state = _condition(expr, build)
                stack.append({"seen": state, "active": state, "expression": expr, "line": number})
            elif not stack:
                diagnostics.append({"line": number, "code": "preprocessor_unmatched_" + kind})
            elif kind == "elseif":
                current = stack[-1]; state = _condition(expr, build)
                current["active"] = False if current["seen"] is True else (state if current["seen"] is False else None)
                current["seen"] = True if current["seen"] is True or state is True else None if current["seen"] is None or state is None else False
                current["expression"] = expr
            elif kind == "else":
                current = stack[-1]
                current["active"] = None if current["seen"] is None else not current["seen"]
                current["expression"] = "else of " + current["expression"]
            else:
                stack.pop()
            lines.append("".join("\n" if ch == "\n" else " " for ch in line))
            continue
        state = False if any(frame["active"] is False for frame in stack) else None if any(frame["active"] is None for frame in stack) else True
        if state is not True:
            entry = {"line": number, "state": "excluded" if state is False else "unknown",
                     "conditions": [frame["expression"] for frame in stack]}
            if regions and regions[-1]["end_line"] == number - 1 and all(regions[-1][k] == entry[k] for k in ("state", "conditions")):
                regions[-1]["end_line"] = number
            else:
                regions.append({**entry, "end_line": number})
        lines.append("".join("\n" if ch == "\n" else " " for ch in line) if state is False else line)
    if stack:
        diagnostics.append({"line": len(lines), "code": "preprocessor_unclosed"})
    return "".join(lines), regions, diagnostics


@dataclass(slots=True)
class Token:
    kind: str
    value: str
    start: int
    end: int


def tokenize(text: str) -> tuple[list[Token], dict[int, int]]:
    tokens, pairs, stack = [], {}, []
    pos, length = 0, len(text)
    while pos < length:
        ch = text[pos]
        if ch.isspace() or ch == "\ufeff":
            pos += 1; continue
        if text.startswith("--", pos):
            long = re.match(r"\[(=*)\[", text[pos + 2:pos + 40])
            if long:
                closing = "]" + long[1] + "]"
                end = text.find(closing, pos + 2 + len(long[0]))
                if end < 0:
                    raise ATTError("unterminated_comment")
                pos = end + len(closing)
            else:
                end = text.find("\n", pos)
                pos = length if end < 0 else end + 1
            continue
        start = pos
        if ch in "\"'":
            pos += 1
            while pos < length:
                if text[pos] == "\\":
                    pos += 2
                elif text[pos] == ch:
                    pos += 1; break
                else:
                    pos += 1
            else:
                raise ATTError("unterminated_string")
            tokens.append(Token("string", text[start + 1:pos - 1], start, pos))
        elif ch == "[" and (long := re.match(r"\[(=*)\[", text[pos:pos + 40])):
            closing = "]" + long[1] + "]"; end = text.find(closing, pos + len(long[0]))
            if end < 0:
                raise ATTError("unterminated_long_string")
            tokens.append(Token("string", text[pos + len(long[0]):end], pos, end + len(closing)))
            pos = end + len(closing)
        elif ch.isalpha() or ch == "_":
            pos += 1
            while pos < length and (text[pos].isalnum() or text[pos] == "_"):
                pos += 1
            tokens.append(Token("name", text[start:pos], start, pos))
        elif ch.isdigit():
            match = NUMBER.match(text, pos)
            assert match
            pos += len(match[0]); tokens.append(Token("number", match[0], start, pos))
        else:
            pos += 1; tokens.append(Token("punct", ch, start, pos))
            index = len(tokens) - 1
            if ch in "({[":
                stack.append((ch, index))
                if len(stack) > 128:
                    raise ATTError("structure_depth_limit")
            elif ch in ")}]":
                if not stack or "({["[")}]".index(ch)] != stack[-1][0]:
                    raise ATTError("unbalanced_structure")
                _, opening = stack.pop(); pairs[opening] = index
        if len(tokens) > MAX_TOKENS:
            raise ATTError("token_limit")
    if stack:
        raise ATTError("unclosed_structure")
    return tokens, pairs


class Reader:
    def __init__(self, text: str, constants: dict[str, Any]):
        self.text = text
        self.tokens, self.pairs = tokenize(text)
        self.constants = constants

    def segments(self, start: int, end: int):
        opening, pos = start, start
        while pos < end:
            if pos in self.pairs:
                pos = self.pairs[pos] + 1; continue
            if self.tokens[pos].value in {",", ";"}:
                if opening < pos:
                    yield opening, pos
                opening = pos + 1
            pos += 1
        if opening < end:
            yield opening, end

    def unknown(self, start: int, end: int) -> dict:
        value = self.text[self.tokens[start].start:self.tokens[end - 1].end]
        return {"unresolved_expression": value[:256], "sha256": hashlib.sha256(value.encode()).hexdigest()}

    def value(self, start: int, end: int, depth: int = 0) -> Any:
        if start >= end:
            return None
        if depth > 10 or end - start > 5000:
            return self.unknown(start, end)
        t = self.tokens
        if end == start + 1:
            token = t[start]
            if token.kind == "string":
                return token.value[:2048]
            if token.kind == "number":
                if re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|\d+)", token.value):
                    return int(token.value, 16 if token.value.lower().startswith("0x") else 10)
                return {"source_number": token.value}  # do not coerce variant IDs to float
            if token.value in {"true", "false", "nil"}:
                return {"true": True, "false": False, "nil": None}[token.value]
            if token.value in self.constants:
                return self.constants[token.value]
        name = "".join(token.value for token in t[start:end])
        if name in self.constants:
            return self.constants[name]
        if t[start].value == "-" and end == start + 2 and t[start + 1].kind == "number":
            v = self.value(start + 1, end, depth + 1)
            return -v if type(v) is int else self.unknown(start, end)
        if t[start].value == "{" and self.pairs.get(start) == end - 1:
            values, mapping = [], {}
            for index, (a, b) in enumerate(self.segments(start + 1, end - 1)):
                if index >= 200:
                    return self.unknown(start, end)
                field = self.field(a, b)
                if field:
                    key, vstart = field
                    mapping[key] = self.value(vstart, b, depth + 1)
                else:
                    values.append(self.value(a, b, depth + 1))
            return {"fields": mapping, "values": values} if mapping else values
        return self.unknown(start, end)

    def field(self, start: int, end: int):
        t = self.tokens
        if start + 2 < end and t[start].kind == "name" and t[start + 1].value == "=":
            return t[start].value, start + 2
        if start + 4 < end and t[start].value == "[" and self.pairs.get(start) == start + 2 and t[start + 3].value == "=":
            key = t[start + 1]
            if key.kind in {"string", "number"}:
                return key.value, start + 4
        return None

    def fields(self, opening: int) -> dict:
        result = {}
        for start, end in self.segments(opening + 1, self.pairs[opening]):
            field = self.field(start, end)
            if field and field[0] in FIELDS:
                result[field[0]] = self.value(field[1], end)
        return result


def _eras(path: str) -> list[str]:
    eras = sorted({era for fragment, era in PATH_ERAS if fragment in path})
    return eras or ["unknown-era"]


def _timeline(fields: list[dict], build: str) -> tuple[list[Any], str]:
    if len(fields) > 1:
        parts = [_timeline([field], build) for field in fields]
        values = [value for part, state in parts for value in part]
        states = [state for part, state in parts]
        return values, "historical" if "historical" in states else "future" if "future" in states else "unverified_current"
    values = [value for row in fields for value in (row.get("timeline") or [])] if all(not row.get("timeline") or isinstance(row["timeline"], list) for row in fields) else []
    events = []
    for value in values:
        if isinstance(value, str) and (match := re.fullmatch(r"(added|removed|deleted|created)\s+(\d+(?:\.\d+)*)", value)):
            events.append((_version(match[2]), match[1]))
    current = _version(build)
    before = sorted((version, event) for version, event in events if version <= current)
    if before and before[-1][1] in {"removed", "deleted"}:
        return values, "historical"
    if not before and any(event == "added" and version > current for version, event in events):
        return values, "future"
    return values, "unverified_current"


def extract_file(data: bytes, path: str, revision: str = ATT_REVISION, build: str = ATT_BUILD,
                 constants: dict[str, Any] | None = None) -> dict:
    if len(data) > MAX_FILE_BYTES:
        raise ATTError("file_size_limit")
    raw = data.decode("utf-8-sig", errors="strict")
    file_sha256 = hashlib.sha256(data).hexdigest()
    text, regions, diagnostics = preprocess(raw, build)
    reader = Reader(text, dict(constants or {}))
    t, pairs = reader.tokens, reader.pairs
    newlines = [index for index, ch in enumerate(text) if ch == "\n"]
    # Only leading top-level local literal declarations, with no later writes.
    # Function bodies, expressions and ambiguous scopes are never evaluated.
    prefix = 0
    while prefix + 3 < len(t) and t[prefix].value == "local" and t[prefix+1].kind == "name" and t[prefix+2].value == "=":
        name, value_start = t[prefix+1].value, prefix + 3
        value_end = pairs.get(value_start, value_start) + 1
        if t[value_start].kind == "name" and value_end < len(t) and t[value_end].value == "(" and value_end in pairs:
            value_end = pairs[value_end] + 1
        value = reader.value(value_start, value_end)
        later_writes = any(t[p].value == name and t[p+1].value == "=" for p in range(value_end, len(t)-1))
        literal_terminated = value_end == len(t) or t[value_end].value == ";"
        if literal_terminated and not later_writes and re.fullmatch(r"[A-Z][A-Z_0-9]+", name) and type(value) in {int, str, bool}:
            reader.constants[name] = value
        if value_end < len(t) and t[value_end].value == ";":
            value_end += 1
        prefix = value_end
    helper_aliases = {}
    for pos in range(3, len(t) - 3):
        if (t[pos].value == "GetRecipeHelperForProfession" and t[pos - 1].value == "="
                and t[pos - 2].kind == "name" and t[pos + 1].value == "(" and pos + 1 in pairs):
            helper_aliases[t[pos - 2].value] = reader.value(pos + 2, pairs[pos + 1])
    # Source-authored profession headings provide taxonomy, not mechanics dates.
    era_headers = []
    era_names = ["Classic", "The Burning Crusade", "Wrath of the Lich King", "Cataclysm", "Mists of Pandaria",
                 "Warlords of Draenor", "Legion", "Battle for Azeroth", "Shadowlands", "Dragonflight", "The War Within", "Midnight"]
    for line_number, source_line in enumerate(raw.splitlines(), 1):
        patch = re.match(r"\s*--\s*PATCH\s+(\d+)\.", source_line, re.I)
        if patch and 1 <= int(patch[1]) <= len(era_names):
            era_headers.append((line_number, era_names[int(patch[1]) - 1]))
        elif re.fullmatch(r"\s*--\s*CLASSIC\s*--\s*", source_line):
            era_headers.append((line_number, "Classic"))
    calls = []
    for pos in range(len(t) - 1):
        if t[pos].kind != "name" or t[pos + 1].value != "(" or pos + 1 not in pairs:
            continue
        if pos > 0 and t[pos - 1].value == "function":
            continue
        end = pairs[pos + 1]
        args = list(reader.segments(pos + 2, end))
        first = reader.value(*args[0]) if args else None
        tables = [a for a, b in args if t[a].value == "{" and pairs.get(a) == b - 1]
        fields = {}
        for opening in tables:
            fields.update(reader.fields(opening))
        actual_constructor = t[pos].value
        helper = actual_constructor in helper_aliases and len(args) >= 2
        if helper:
            fields["recipeItemID"] = first if first != 0 else None
            fields["requireSkill"] = helper_aliases[actual_constructor]
            first = reader.value(*args[1]); fields["recipeID"] = first
            if len(args) >= 3:
                unobtainable = reader.value(*args[2])
                if isinstance(unobtainable, (list, str)):
                    fields["timeline"] = [unobtainable] if isinstance(unobtainable, str) else unobtainable
                else:
                    fields["u"] = unobtainable
            if len(args) >= 4:
                fields["requireSkill"] = reader.value(*args[3])
        if actual_constructor == "salvagerecipe" and len(args) > 1:
            fields["displayItemID"] = reader.value(*args[1])
        calls.append({"start": pos, "end": end, "constructor": t[pos].value, "id": first,
                      "fields": fields, "helper": helper, "line": bisect_right(newlines, t[pos].start) + 1})
    stack, candidates, dispositions = [], [], Counter()
    identities = Counter()
    for call in calls:
        while stack and stack[-1]["end"] < call["start"]:
            stack.pop()
        ancestors = stack[-16:]
        fields = call["fields"]
        constructor = call["constructor"]
        identifier = call["id"]
        nested_items = []
        # Direct structural children are indexed below without O(n^2) scans.
        call["parent"] = stack[-1] if stack else None
        call["children"] = []
        if stack:
            stack[-1]["children"].append(call)
        call["ancestors"] = list(ancestors)
        stack.append(call)
    for call in calls:
        fields, constructor, identifier = call["fields"], call["constructor"], call["id"]
        ancestors = call["ancestors"]
        entity_kind = "recipe" if call["helper"] else ID_KINDS.get(constructor, "unknown")
        descendants, nested = list(reversed(call["children"])), []
        while descendants and len(nested) <= 200:
            child = descendants.pop()
            if ID_KINDS.get(child["constructor"]) == "item" and not child["helper"]:
                nested.append(child)
            descendants.extend(reversed(child["children"]))
        periodic = any(key in fields for key in ("isDaily", "isWeekly", "isMonthly", "isYearly", "repeatable", "isRepeatable"))
        cost = fields.get("cost", fields.get("_cost"))
        profession = any(part in path for part in ("00 - Profession DB", "Professions", "Craftables")) or any(a["constructor"] in {"prof", "profession"} for a in ancestors)
        vendor = any(a["constructor"] == "n" and type(a["id"]) is int and a["id"] > 0 for a in ancestors)
        semantic = (entity_kind == "recipe" or cost is not None or periodic or "reagents" in fields or
                    (entity_kind == "item" and (nested or "sym" in fields or profession or vendor)) or
                    (entity_kind == "unknown" and nested))
        if not semantic:
            dispositions["non_economic_constructor"] += 1; continue
        if not isinstance(identifier, (int, dict)) or type(identifier) is bool:
            dispositions["unsupported_identity"] += 1; continue
        line = call["line"]
        inherited = [{"constructor": a["constructor"], "id": a["id"], "line": a["line"],
                      "fields": {k: v for k, v in a["fields"].items() if k != "description"}}
                     for a in ancestors if a["fields"] or a["constructor"] in ID_KINDS or a["constructor"] in {"bubbleDown", "sharedData"}]
        timeline, applicability = _timeline([a["fields"] for a in ancestors] + [fields], build)
        unknown_regions = [region for region in regions if region["state"] == "unknown" and region["line"] <= line <= region["end_line"]]
        family = "salvage_recipe" if constructor == "salvagerecipe" else "professional_recipe" if entity_kind == "recipe" else "purchase_or_exchange" if cost is not None else "periodic_candidate" if periodic else "container_relation" if constructor == "container" else "item_relation" if entity_kind == "item" else "unknown_constructor"
        unknowns = ["mechanic_not_runtime_validated", "yield_quantities", "joint_outcomes", "binding", "tradeability", "current_server_applicability"]
        if nested:
            unknowns.append("nested_collection_links_are_not_guaranteed_outputs")
        if "sym" in fields:
            unknowns.append("symbolic_reference_unresolved")
        if unknown_regions:
            unknowns.append("preprocessor_condition_unresolved")
        if any(a["constructor"] in {"bubbleDown", "sharedData"} for a in ancestors):
            unknowns.append("wrapper_inheritance_requires_semantic_validation")
        if isinstance(identifier, dict):
            unknowns.append("entity_id_expression_unresolved")
        if call["helper"]:
            unknowns.append("recipe_item_association_not_crafted_output")
        status = "historical" if applicability == "historical" else "quarantined" if applicability == "future" or unknown_regions or isinstance(identifier, dict) else "candidate"
        claim = {"constructor": constructor, "entity_kind": entity_kind, "entity_id": identifier,
                 "explicit_fields": fields, "ancestor_context": inherited,
                 "related_items": [{"item_id": child["id"], "line": child["line"], "relation": "nested_collection_link", "quantity": None}
                                   for child in nested[:200]],
                 "related_items_truncated": len(nested) > 200,
                 "cost_field": "cost" if "cost" in fields else "_cost" if "_cost" in fields else None,
                 "cost_claim": cost}
        identity = json.dumps([path, constructor, identifier, [[a["constructor"], a["id"]] for a in ancestors
                             if a["constructor"] in ID_KINDS]], sort_keys=True, ensure_ascii=False)
        identity_hash = hashlib.sha256(identity.encode()).hexdigest()
        identities[identity_hash] += 1
        key = "att:" + identity_hash + ":" + str(identities[identity_hash])
        eras = _eras(path)
        preceding_headers = [era for header_line, era in era_headers if header_line <= line]
        if "00 - Profession DB/" in path and preceding_headers:
            eras = [preceding_headers[-1]]
        candidates.append({"key": key, "family": family, "status": status, "source": {"path": path, "line": line,
                           "sha256": file_sha256, "revision": revision}, "claim": claim,
                           "applicability": {"game": "retail", "build": build, "state": applicability, "timeline": timeline,
                                             "unknown_conditions": unknown_regions, "expansions": eras},
                           "unknowns": unknowns})
        dispositions[status] += 1
    return {"candidates": candidates, "constructor_count": len(calls), "constructor_dispositions": dict(dispositions),
            "preprocessor_regions": regions, "diagnostics": diagnostics, "tokens": len(t),
            "symbol_definitions": [{"name": match[1], "line": bisect_right(newlines, match.start()) + 1}
                                   for match in re.finditer(r"(?m)^[ \t]*(?:local[ \t]+)?([A-Z][A-Z_0-9]+)[ \t]*=", text)]}


def _constants(files: list[tuple[str, bytes]], build: str) -> dict:
    """Only unambiguous literal constants; no evaluation of functions/arithmetic."""
    values = defaultdict(list)
    for path, data in files:
        if "/constants/" not in path.lower() and "/lib/constants/" not in path.lower():
            continue
        try:
            text, _, _ = preprocess(data.decode("utf-8-sig"), build)
            reader = Reader(text, {})
            tokens = reader.tokens
            for pos in range(len(tokens) - 2):
                if tokens[pos].kind != "name" or tokens[pos+1].value != "=":
                    continue
                end = reader.pairs.get(pos + 2, pos + 2) + 1
                value = reader.value(pos + 2, end)
                literal_terminated = end == len(tokens) or tokens[end].value in {";", ",", "}"}
                if literal_terminated and type(value) in {int, str, bool}:
                    values[tokens[pos].value].append(value)
                elif isinstance(value, dict) and "fields" in value:
                    for key, field in value["fields"].items():
                        if type(field) in {int, str, bool}:
                            values[tokens[pos].value + "." + key].append(field)
        except (ATTError, UnicodeError):
            continue
    return {name: rows[0] for name, rows in values.items() if all(type(value) is type(rows[0]) and value == rows[0] for value in rows)}


def scan_corpus(root: Path, revision: str = ATT_REVISION, build: str = ATT_BUILD) -> dict:
    root = Path(root).resolve()
    tree = json.loads((root / "tree.json").read_text(encoding="utf-8"))
    if tree.get("truncated") is not False:
        raise ATTError("tree_truncated_or_unknown")
    if revision == ATT_REVISION and tree.get("sha") != ATT_TREE:
        raise ATTError("pinned_root_tree_mismatch")
    if revision != ATT_REVISION:
        try:
            commit = json.loads((root / "commit.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ATTError("source_commit_proof_missing") from exc
        if commit.get("sha") != revision or commit.get("tree", {}).get("sha") != tree.get("sha"):
            raise ATTError("source_commit_tree_mismatch")
    entries = [entry for entry in tree["tree"] if entry["type"] == "blob" and include_path(entry["path"])]
    if len({entry["path"].casefold() for entry in entries}) != len(entries):
        raise ATTError("duplicate_or_case_colliding_paths")
    standard = [e for e in entries if e["path"].startswith(ATT_STANDARD) and not "/.config/" in e["path"] and e["path"].endswith(".lua")]
    if revision == ATT_REVISION and len(standard) != 1874:
        raise ATTError("pinned_standard_denominator_mismatch")
    files, sources = [], []
    for entry in entries:
        path = entry["path"]; relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or ":" in path or "\\" in path:
            raise ATTError("unsafe_source_path")
        local = root.joinpath(*relative.parts)
        if not local.resolve().is_relative_to(root):
            raise ATTError("unsafe_source_target")
        row = {"path": path, "git_blob_sha1": entry["sha"], "expected_bytes": entry.get("size"),
               "disposition": "missing", "sha256": None, "candidate_count": 0}
        files.append(row)
        if not local.is_file():
            continue
        data = local.read_bytes()
        row["sha256"] = hashlib.sha256(data).hexdigest(); row["bytes"] = len(data)
        if len(data) > MAX_FILE_BYTES or git_blob(data) != entry["sha"]:
            row["disposition"] = "hash_mismatch" if len(data) <= MAX_FILE_BYTES else "file_size_limit"
            continue
        row["disposition"] = "fetched"
        if path.endswith(".lua"):
            sources.append((path, data))
    constants = _constants(sources, build)
    candidates, counts, coverage = [], Counter(), defaultdict(Counter)
    lookup = {path: data for path, data in sources}
    for row in files:
        if row["disposition"] != "fetched":
            continue
        path = row["path"]
        if not path.endswith(".lua"):
            row["disposition"] = "license_notice" if path == "LICENSE" else "configuration_recorded"
            continue
        try:
            result = extract_file(lookup[path], path, revision, build, constants)
            if len(candidates) + len(result["candidates"]) > MAX_CANDIDATES:
                raise ATTError("corpus_candidate_limit")
            candidates.extend(result.pop("candidates"))
            row.update(result)
            row["candidate_count"] = sum(row["constructor_dispositions"].get(k, 0) for k in ("candidate", "historical", "quarantined"))
            row["disposition"] = "normalized_candidates" if row["candidate_count"] else "no_candidates"
            if row["diagnostics"]:
                row["disposition"] = "quarantined"
            counts.update(row["constructor_dispositions"])
        except (ATTError, UnicodeError, RecursionError) as exc:
            row["disposition"] = "unsupported"
            row["reason"] = str(exc) if isinstance(exc, ATTError) else type(exc).__name__
        for era in _eras(path):
            coverage[era][row["disposition"]] += 1
    status_counts = Counter(row["disposition"] for row in files)
    complete = not any(row["disposition"] in {"missing", "hash_mismatch", "file_size_limit", "fetched"} for row in files)
    families = Counter(candidate["family"] for candidate in candidates)
    semantic_gaps = Counter(unknown for candidate in candidates for unknown in candidate["unknowns"])
    definitions = defaultdict(list)
    for row in files:
        for symbol in row.get("symbol_definitions", []):
            definitions[symbol["name"]].append({"path": row["path"], "line": symbol["line"], "sha256": row["sha256"]})
    reference_counts = Counter()
    for candidate in candidates:
        symbol = candidate["claim"]["explicit_fields"].get("sym")
        if not isinstance(symbol, dict):
            continue
        name = symbol.get("unresolved_expression", "")
        if not re.fullmatch(r"[A-Z][A-Z_0-9]+", name):
            continue
        same_file = [definition for definition in definitions.get(name, []) if definition["path"] == candidate["source"]["path"]]
        locations = same_file or definitions.get(name, [])
        candidate["claim"]["symbol_reference"] = {"name": name, "definitions": locations[:20],
                "status": "definition_located_semantics_unresolved" if locations else "definition_not_found",
                "truncated": len(locations) > 20}
        reference_counts[candidate["claim"]["symbol_reference"]["status"]] += 1
    scope_dispositions = [{"path": entry["path"], "git_blob_sha1": entry["sha"], "bytes": entry.get("size"),
                           **dependency_disposition(entry["path"])}
                          for entry in tree["tree"] if entry["type"] == "blob" and
                          (entry["path"].startswith(ATT_STANDARD) or entry["path"].startswith(ATT_SHARED)) and not include_path(entry["path"])]
    expansion_families = defaultdict(Counter)
    for candidate in candidates:
        for era in candidate["applicability"]["expansions"]:
            expansion_families[era][candidate["family"]] += 1
    return {"source_id": "att-standard", "source": source_policy(revision, tree.get("sha")), "revision": revision, "build": build,
            "parser_revision": PARSER_REVISION, "complete": complete, "mechanics_complete": False,
            "denominator": len(entries), "standard_lua_denominator": len(standard), "tree_entries": len(tree["tree"]),
            "tree_sha256": hashlib.sha256((root / "tree.json").read_bytes()).hexdigest(), "files": files,
            "candidates": candidates, "coverage": {"file_dispositions": dict(status_counts),
            "constructor_dispositions": dict(counts), "candidate_families": dict(families),
            "expansion_files": {k: dict(v) for k, v in sorted(coverage.items())},
            "resolved_unambiguous_constants": len(constants), "candidate_unknowns": dict(semantic_gaps),
            "symbol_references": dict(reference_counts),
            "expansion_candidate_families": {k: dict(v) for k, v in sorted(expansion_families.items())},
            "all_game_percentage": None, "confirmed_current_transformations": 0},
            "scope_dispositions": scope_dispositions, "gaps": source_policy()["blocked"]}
