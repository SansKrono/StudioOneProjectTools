import re


def find_balanced_attributes_blocks(xml_text: str, predicate) -> list[tuple[int, int, str]]:
    """
    Finds XML-ish <Attributes ...>...</Attributes> blocks and returns blocks matching predicate(opening_tag).

    Studio One .song XML is regular enough for this controlled block scanner.
    It is safer here than trying to parse potentially namespace-heavy fragmented XML.
    """
    open_pat = re.compile(r"<Attributes\b[^>]*>")
    close_pat = re.compile(r"</Attributes>")

    tokens = []
    for m in open_pat.finditer(xml_text):
        tokens.append((m.start(), m.end(), "open", m.group(0)))
    for m in close_pat.finditer(xml_text):
        tokens.append((m.start(), m.end(), "close", m.group(0)))
    tokens.sort(key=lambda x: x[0])

    stack = []
    results = []

    for start, end, kind, tag in tokens:
        if kind == "open":
            stack.append((start, end, tag))
        else:
            if not stack:
                continue
            open_start, open_end, open_tag = stack.pop()
            block_end = end
            if predicate(open_tag):
                results.append((open_start, block_end, xml_text[open_start:block_end]))

    return results


def remove_ranges(text: str, ranges: list[tuple[int, int]]) -> str:
    if not ranges:
        return text

    ranges = sorted(ranges, key=lambda x: x[0])
    out = []
    last = 0

    for start, end in ranges:
        if start < last:
            continue
        out.append(text[last:start])
        last = end

    out.append(text[last:])
    return "".join(out)
