"""
Rewrite audio references inside .song text members.

All replacements are applied in a single regex pass, so text that has already
been replaced is never scanned again (the previous chained str.replace() calls
could turn "Old\\a.wav" into "Old\\Media\\a.wav").
"""

from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import quote, unquote


def styled_like(new_ref: str, old_ref: str) -> str:
    """Write new_ref with the same separator style as old_ref."""
    if "\\\\" in old_ref:
        return new_ref.replace("\\", "\\\\")
    if "/" in old_ref and "\\" not in old_ref:
        return new_ref.replace("\\", "/")
    return new_ref


def path_variants(value: str) -> set[str]:
    """Spellings of one path that may appear in serialized Studio One XML."""
    if not value:
        return set()

    variants = {value, value.replace("\\", "/"), value.replace("/", "\\")}
    decoded = unquote(value)
    variants |= {decoded, decoded.replace("\\", "/"), decoded.replace("/", "\\")}

    for v in list(variants):
        variants.add(quote(v, safe="/:\\._-()[]{} @#$%&+=,;~!"))

    for v in list(variants):
        variants.add(v.replace("\\", "\\\\"))

    return {v for v in variants if v}


def is_bare_filename(ref: str) -> bool:
    return "/" not in ref and "\\" not in ref and ":" not in ref


def relative_ref(dest: Path, base_dir: Path) -> str:
    """Windows-style reference to dest, relative to base_dir when possible."""
    try:
        return str(dest.resolve().relative_to(base_dir.resolve())).replace("/", "\\")
    except ValueError:
        return str(dest.resolve()).replace("/", "\\")


class Relinker:
    """
    Collects old-reference -> new-reference rules and applies them to text.

    - add_path(old, new): replaces every spelling of the full path old.
    - add_filename(name, new): replaces a quoted *relative* path value that ends
      in name (e.g. "Old Takes\\name"). Absolute paths and URLs are never touched
      by filename rules. Only use this for files confirmed missing everywhere in
      the project, so no existing reference can end in the same name.
    """

    def __init__(self):
        self._exact: dict[str, str] = {}
        self._by_name: dict[str, str] = {}
        self._pattern: re.Pattern | None = None

    def __bool__(self) -> bool:
        return bool(self._exact or self._by_name)

    def add_path(self, old_ref: str, new_ref: str):
        if not old_ref or is_bare_filename(old_ref):
            return
        for old in path_variants(old_ref):
            self._exact[old] = styled_like(new_ref, old)
        self._pattern = None

    def add_filename(self, name: str, new_ref: str):
        if name:
            self._by_name[name.lower()] = new_ref
        self._pattern = None

    def add_reference(self, old_ref: str, new_ref: str):
        if is_bare_filename(old_ref):
            self.add_filename(old_ref, new_ref)
        else:
            self.add_path(old_ref, new_ref)

    def _compile(self) -> re.Pattern:
        parts = []
        if self._exact:
            keys = sorted(self._exact, key=len, reverse=True)
            parts.append("(?P<exact>" + "|".join(re.escape(k) for k in keys) + ")")
        if self._by_name:
            names = sorted(self._by_name, key=len, reverse=True)
            parts.append(
                r'(?P<q>["\'])(?P<dir>(?:[^"\'<>:\r\n]*[\\/])?)'
                "(?P<name>(?i:" + "|".join(re.escape(n) for n in names) + "))"
                r"(?P=q)"
            )
        return re.compile("|".join(parts))

    def apply(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        """Return (new_text, [(old, new), ...] for every replacement made)."""
        if not self:
            return text, []
        if self._pattern is None:
            self._pattern = self._compile()

        hits: list[tuple[str, str]] = []

        def repl(m: re.Match) -> str:
            old = m.groupdict().get("exact")
            if old is not None:
                new = self._exact[old]
            else:
                quote_char = m.group("q")
                old_value = m.group("dir") + m.group("name")
                new_value = styled_like(self._by_name[m.group("name").lower()], old_value)
                old, new = quote_char + old_value + quote_char, quote_char + new_value + quote_char
            if old != new:
                hits.append((old, new))
            return new

        return self._pattern.sub(repl, text), hits
