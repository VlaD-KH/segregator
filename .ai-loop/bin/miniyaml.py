"""Dependency-free loader for the restricted YAML subset used by ai-loop policy files.

The control plane must be enforceable on any machine, in any repo, with nothing
installed but Python 3. Requiring PyYAML would make enforcement conditional on a
package install -- and a control plane that silently degrades when a dependency
is missing is not a control plane.

If PyYAML *is* available it is used (it is strictly more correct). Otherwise this
parser handles the subset the ai-loop templates emit:

    key: value
    key:
      nested: value
    key:
      - item
      - key: value
        other: value
    flow_list: [A, B, C]
    flow_map: {threshold: 25, bump: 1}

Anything outside that subset raises MiniYamlError instead of guessing. Guessing is
how a policy file ends up meaning something other than what its author read.
"""

from __future__ import annotations

import re
from typing import Any


class MiniYamlError(ValueError):
    """Raised when input falls outside the supported subset."""


def load(text: str) -> Any:
    """Parse YAML text. Uses PyYAML when importable, else the built-in subset parser."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return _load_subset(text)
    return yaml.safe_load(text)


def load_path(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return load(handle.read())


# --------------------------------------------------------------------------- #
# Subset parser
# --------------------------------------------------------------------------- #

_KEY_RE = re.compile(r"^(?P<key>[^\s:][^:]*?)\s*:(?:\s+(?P<value>.*))?$")


def _load_subset(text: str) -> Any:
    lines = _tokenize(text)
    if not lines:
        return None
    value, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise MiniYamlError(f"unconsumed input at line {lines[index][2]}")
    return value


def _tokenize(text: str) -> list[tuple[int, str, int, str]]:
    """Return (indent, comment-stripped content, line_number, raw content) per line.

    The raw form is kept because block scalars (`key: >` / `key: |`) carry prose,
    where a `#` is a character rather than a comment.
    """
    out: list[tuple[int, str, int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if raw.strip().startswith("#") or not raw.strip():
            continue
        if raw.lstrip().startswith("---"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent]:
            raise MiniYamlError(f"tab indentation at line {number}")
        stripped = raw.strip()
        out.append((indent, _strip_comment(stripped), number, stripped))
    return out


_BLOCK_SCALAR = {">", "|", ">-", "|-", ">+", "|+"}


def _parse_block_scalar(lines, index: int, indent: int, style: str) -> tuple[str, int]:
    """Consume the indented body of a `>` or `|` scalar."""
    parts: list[str] = []
    while index < len(lines) and lines[index][0] > indent:
        parts.append(lines[index][3])
        index += 1
    joiner = "\n" if style.startswith("|") else " "
    value = joiner.join(parts)
    if style.endswith("+"):
        value += "\n"
    elif not style.endswith("-") and value:
        value += "\n" if style.startswith("|") else ""
    return value, index


def _strip_comment(value: str) -> str:
    """Drop a trailing comment, respecting quotes and flow collections."""
    quote: str | None = None
    depth = 0
    for i, ch in enumerate(value):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth = max(0, depth - 1)
        elif ch == "#" and depth == 0 and (i == 0 or value[i - 1] in " \t"):
            return value[:i].rstrip()
    return value


def _parse_block(lines, index: int, indent: int):
    content = lines[index][1]
    if content == "-" or content.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_map(lines, index, indent)


def _parse_map(lines, index: int, indent: int):
    result: dict[str, Any] = {}
    while index < len(lines):
        cur_indent, content, number = lines[index][0], lines[index][1], lines[index][2]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            raise MiniYamlError(f"unexpected indentation at line {number}")
        match = _KEY_RE.match(content)
        if not match:
            raise MiniYamlError(f"expected 'key: value' at line {number}: {content!r}")
        key = _scalar(match.group("key"))
        raw_value = match.group("value")
        index += 1
        if raw_value is not None and raw_value.strip() in _BLOCK_SCALAR:
            result[key], index = _parse_block_scalar(lines, index, cur_indent, raw_value.strip())
            continue
        if raw_value not in (None, ""):
            result[key] = _scalar(raw_value)
            continue
        if index < len(lines) and lines[index][0] > indent:
            child, index = _parse_block(lines, index, lines[index][0])
            result[key] = child
        else:
            result[key] = None
    return result, index


def _parse_list(lines, index: int, indent: int):
    result: list[Any] = []
    while index < len(lines):
        cur_indent, content, number = lines[index][0], lines[index][1], lines[index][2]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            raise MiniYamlError(f"unexpected indentation at line {number}")
        if not (content == "-" or content.startswith("- ")):
            break
        item = content[1:].strip()
        index += 1
        if not item:
            if index < len(lines) and lines[index][0] > indent:
                child, index = _parse_block(lines, index, lines[index][0])
                result.append(child)
            else:
                result.append(None)
            continue
        match = _KEY_RE.match(item)
        if match:
            # "- key: value" starts an inline map; sibling keys follow at the
            # column where the key itself begins.
            entry_indent = cur_indent + (len(content) - len(content[1:].lstrip()))
            entry: dict[str, Any] = {}
            key = _scalar(match.group("key"))
            raw_value = match.group("value")
            if raw_value is not None and raw_value.strip() in _BLOCK_SCALAR:
                entry[key], index = _parse_block_scalar(lines, index, entry_indent, raw_value.strip())
            elif raw_value not in (None, ""):
                entry[key] = _scalar(raw_value)
            elif index < len(lines) and lines[index][0] > entry_indent:
                child, index = _parse_block(lines, index, lines[index][0])
                entry[key] = child
            else:
                entry[key] = None
            while index < len(lines) and lines[index][0] == entry_indent and not lines[index][1].startswith("- "):
                rest, index = _parse_map(lines, index, entry_indent)
                entry.update(rest)
                break
            result.append(entry)
        else:
            result.append(_scalar(item))
    return result, index


def _scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(part) for part in _split_flow(value[1:-1])] if value[1:-1].strip() else []
    if value.startswith("{") and value.endswith("}"):
        out: dict[str, Any] = {}
        for part in _split_flow(value[1:-1]):
            if not part.strip():
                continue
            if ":" not in part:
                raise MiniYamlError(f"flow map entry without ':': {part!r}")
            key, _, val = part.partition(":")
            out[_scalar(key)] = _scalar(val)
        return out
    lowered = value.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~", ""):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _split_flow(value: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    current: list[str] = []
    for ch in value:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch in "[{":
            depth += 1
            current.append(ch)
        elif ch in "]}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts
