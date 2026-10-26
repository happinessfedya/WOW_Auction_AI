"""Bounded parser for the data subset written to WoW SavedVariables.

This deliberately is not a Lua interpreter. Only global assignments of literal
values and tables are accepted. Unsupported syntax fails closed. Strings are
strict UTF-8, including bytes assembled from Lua decimal escapes; binary strings
are quarantined rather than decoded with replacement characters.
"""

from dataclasses import dataclass
import math
import re
import time
from typing import Any


@dataclass(frozen=True)
class ParseLimits:
    max_bytes: int = 32 * 1024 * 1024
    max_depth: int = 80
    max_nodes: int = 1_000_000
    max_string_bytes: int = 8 * 1024 * 1024
    max_number_chars: int = 128
    max_seconds: float = 5.0

    def __post_init__(self) -> None:
        integers = (self.max_bytes, self.max_depth, self.max_nodes,
                    self.max_string_bytes, self.max_number_chars)
        if any(type(value) is not int or value <= 0 for value in integers):
            raise ValueError("parser_limits_must_be_positive_integers")
        # Recursive descent must remain below the Python interpreter stack limit.
        if self.max_depth > 100:
            raise ValueError("parser_depth_limit_exceeds_safe_maximum")
        if not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError("parser_time_limit_must_be_positive")


class LuaDataError(ValueError):
    """A safe diagnostic: contains no source text or personal values."""

    def __init__(self, code: str, offset: int = 0):
        self.code = code
        self.offset = offset
        super().__init__(f"{code} at byte {offset}")


_NAME = re.compile(rb"[A-Za-z_][A-Za-z_0-9]*")
_NUMBER = re.compile(rb"-?(?:0[xX][0-9a-fA-F]+|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)")
_RESERVED = set(b"and break do else elseif end false for function if in local nil not or repeat return then true until while".split())
_ESCAPES = {ord("a"): 7, ord("b"): 8, ord("f"): 12, ord("n"): 10,
            ord("r"): 13, ord("t"): 9, ord("v"): 11,
            ord("\\"): 92, ord('"'): 34, ord("'"): 39}


class _Parser:
    def __init__(self, data: bytes, limits: ParseLimits):
        self.data = data
        self.limits = limits
        self.pos = 3 if data.startswith(b"\xef\xbb\xbf") else 0
        self.nodes = 0
        self.deadline = time.monotonic() + limits.max_seconds

    def fail(self, code: str) -> None:
        raise LuaDataError(code, self.pos)

    def budget(self) -> None:
        if time.monotonic() >= self.deadline:
            self.fail("time_limit")

    def long_open(self, pos: int) -> tuple[int, bytes] | None:
        if self.data[pos:pos + 1] != b"[":
            return None
        end = pos + 1
        while end < len(self.data) and self.data[end] == 61:
            end += 1
            if (end - pos) % 4096 == 0:
                self.budget()
        if self.data[end:end + 1] != b"[":
            return None
        return end + 1, b"]" + self.data[pos + 1:end] + b"]"

    def long_bytes(self) -> bytes:
        opened = self.long_open(self.pos)
        if opened is None:
            self.fail("expected_long_string")
        start, close = opened
        end = self.data.find(close, start)
        if end < 0:
            self.fail("unterminated_long_string")
        raw = self.data[start:end]
        self.pos = end + len(close)
        self.budget()
        # Lua normalizes all physical newline forms, and omits the first one.
        raw = re.sub(rb"\r\n|\n\r|\r|\n", b"\n", raw)
        return raw[1:] if raw.startswith(b"\n") else raw

    def skip(self) -> None:
        while self.pos < len(self.data):
            self.budget()
            if self.data[self.pos] in b" \t\r\n\v\f":
                self.pos += 1
            elif self.data[self.pos:self.pos + 2] == b"--":
                self.pos += 2
                if self.long_open(self.pos):
                    self.long_bytes()
                else:
                    while self.pos < len(self.data) and self.data[self.pos] not in b"\r\n":
                        self.pos += 1
                        if self.pos % 4096 == 0:
                            self.budget()
            else:
                return

    def consume(self, token: bytes) -> None:
        self.skip()
        if self.data[self.pos:self.pos + len(token)] != token:
            self.fail("unexpected_token")
        self.pos += len(token)

    def name(self) -> str:
        self.skip()
        match = _NAME.match(self.data, self.pos)
        if match is None or match[0] in _RESERVED:
            self.fail("expected_identifier")
        self.pos = match.end()
        return match[0].decode("ascii")

    def string(self) -> str:
        if self.long_open(self.pos):
            raw = self.long_bytes()
            if len(raw) > self.limits.max_string_bytes:
                self.fail("string_limit")
        else:
            quote = self.data[self.pos]
            self.pos += 1
            out = bytearray()
            while True:
                if self.pos >= len(self.data):
                    self.fail("unterminated_string")
                c = self.data[self.pos]
                self.pos += 1
                if c == quote:
                    break
                if c in (10, 13):
                    self.fail("unescaped_newline")
                if c == 92:
                    if self.pos >= len(self.data):
                        self.fail("unterminated_escape")
                    c = self.data[self.pos]
                    self.pos += 1
                    if 48 <= c <= 57:
                        digits = bytes([c])
                        for _ in range(2):
                            if self.pos < len(self.data) and 48 <= self.data[self.pos] <= 57:
                                digits += self.data[self.pos:self.pos + 1]
                                self.pos += 1
                            else:
                                break
                        c = int(digits)
                        if c > 255:
                            self.fail("decimal_escape_out_of_range")
                    elif c in (10, 13):
                        if self.pos < len(self.data) and self.data[self.pos] in (10, 13) and self.data[self.pos] != c:
                            self.pos += 1
                        c = 10
                    elif c in _ESCAPES:
                        c = _ESCAPES[c]
                    else:
                        self.fail("unsupported_escape")
                out.append(c)
                if len(out) > self.limits.max_string_bytes:
                    self.fail("string_limit")
                if len(out) % 4096 == 0:
                    self.budget()
            raw = bytes(out)
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            self.fail("invalid_string_utf8")

    def value(self, depth: int = 0) -> Any:
        self.skip()
        self.budget()
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            self.fail("node_limit")
        if self.pos >= len(self.data):
            self.fail("unexpected_end")
        c = self.data[self.pos]
        if c == 123:
            if depth >= self.limits.max_depth:
                self.fail("depth_limit")
            return self.table(depth + 1)
        if c in (34, 39) or self.long_open(self.pos):
            return self.string()
        match = _NUMBER.match(self.data, self.pos)
        if match:
            token = match[0]
            if len(token) > self.limits.max_number_chars:
                self.fail("number_limit")
            self.pos = match.end()
            if self.pos < len(self.data) and self.data[self.pos] in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_0123456789.":
                self.fail("malformed_number")
            if b"x" in token.lower():
                return int(token, 16)
            if any(part in token for part in (b".", b"e", b"E")):
                value = float(token)
                if not math.isfinite(value):
                    self.fail("nonfinite_number")
                return value
            return int(token)
        match = _NAME.match(self.data, self.pos)
        if match and match[0] in (b"true", b"false", b"nil"):
            self.pos = match.end()
            return {b"true": True, b"false": False, b"nil": None}[match[0]]
        self.fail("unsupported_value")

    def table(self, depth: int) -> dict:
        self.consume(b"{")
        result = {}
        seen = set()
        implicit_index = 1
        while True:
            self.skip()
            if self.data[self.pos:self.pos + 1] == b"}":
                self.pos += 1
                return result
            if self.data[self.pos:self.pos + 1] == b"[" and not self.long_open(self.pos):
                self.pos += 1
                key = self.value(depth)
                if type(key) is float and key.is_integer():
                    key = int(key)
                if type(key) not in (int, str):
                    self.fail("unsupported_table_key")
                self.consume(b"]")
                self.consume(b"=")
                val = self.value(depth)
            else:
                before = self.pos
                match = _NAME.match(self.data, self.pos)
                named = False
                if match and match[0] not in _RESERVED:
                    self.pos = match.end()
                    self.skip()
                    named = self.data[self.pos:self.pos + 1] == b"="
                if named:
                    key = match[0].decode("ascii")
                    self.pos += 1
                    val = self.value(depth)
                else:
                    self.pos = before
                    key = implicit_index
                    implicit_index += 1
                    val = self.value(depth)
            if key in seen:
                self.fail("duplicate_table_key")
            seen.add(key)
            if val is not None:
                result[key] = val
            self.skip()
            if self.data[self.pos:self.pos + 1] in (b",", b";"):
                self.pos += 1
            elif self.data[self.pos:self.pos + 1] != b"}":
                self.fail("expected_table_separator")

    def parse(self) -> dict[str, Any]:
        result = {}
        seen = set()
        self.skip()
        while self.pos < len(self.data):
            key = self.name()
            if key in seen:
                self.fail("duplicate_assignment")
            seen.add(key)
            self.consume(b"=")
            value = self.value()
            if value is not None:
                result[key] = value
            self.skip()
            if self.data[self.pos:self.pos + 1] == b";":
                self.pos += 1
            self.skip()
        self.budget()
        return result


def parse_saved_variables(data: bytes, limits: ParseLimits | None = None) -> dict[str, Any]:
    """Parse global literal assignments, never execute Lua or lose invalid bytes.

    All tables are dictionaries, including arrays (Lua uses one-based indices).
    Nil values are absent, while false and zero are preserved. Duplicate fields
    and global assignments are rejected instead of guessing overwrite order.
    """
    limits = limits or ParseLimits()
    if not isinstance(data, bytes):
        raise TypeError("saved_variables_requires_bytes")
    if len(data) > limits.max_bytes:
        raise LuaDataError("size_limit")
    parser = _Parser(data, limits)
    try:
        data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        raise LuaDataError("invalid_file_utf8", error.start) from None
    return parser.parse()
