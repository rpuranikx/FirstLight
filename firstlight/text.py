"""Small text helpers shared by every stage: HTML to text, pay and years parsing."""
import html
import re

_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<(br|/p|/li|/h\d|/div)[^>]*>", re.I)


def plain_text(raw: str) -> str:
    text = _BREAK.sub("\n", raw or "")
    text = html.unescape(_TAG.sub("", html.unescape(text)))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


# "$120,000 - $150,000", "$120K–$150K", "USD 140k"
_MONEY = re.compile(r"(?:\$|USD\s?)\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d)?\s?[kK])\b")


def _to_int(s: str) -> int:
    s = s.replace(",", "").strip()
    if s[-1] in "kK":
        return int(float(s[:-1]) * 1000)
    return int(s)


def pay_from_text(text: str) -> dict:
    """
    {"max": int | None, "ote": bool} for the largest yearly figure in the text.
    OTE is flagged when the posting says the range is on-target earnings, since
    a $150k OTE can mean a ~$105k base.
    """
    values = [v for v in (_to_int(m) for m in _MONEY.findall(text or "")) if 40_000 <= v <= 900_000]
    ote = bool(re.search(r"\bOTE\b|on[- ]target earnings|base \+ (?:variable|commission)", text or "", re.I))
    return {"max": max(values) if values else None, "ote": ote}


# "5+ years", "3-5 years", "at least 4 years", "Minimum of 6 years"
YEARS_RE = re.compile(r"(?<![\d.$])(\d{1,2})\s*\+?\s*(?:(?:-+|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)(?:'|’)?\b(?!\s+(?:old|ago|of age|to exercise))", re.I)
NOT_A_REQUIREMENT = re.compile(r"founded|company|history|in business|track record|over the (?:past|last)|"
                               r"for (?:the )?(?:past|last)|since|anniversary|customers for|we've been|founders|worked together|"
                               r"with more than|serving|trusted by", re.I)
REQUIREMENT_AFTER = re.compile(r"^.{0,60}\b(experience|exp\b|in |of |as |working|selling|background|track|hands-on|"
                               r"building|leading|managing|supporting|designing|deploying|professional|industry|"
                               r"customer|technical|sales)", re.I | re.S)


NICE_TO_HAVE = re.compile(r"prefer|nice to have|bonus|a plus|ideally|desired", re.I)


def years_required(description: str) -> int | None:
    """
    The largest hard "N+ years" the posting asks for. "2+ years of Python and 5+ years
    in presales" means 5. Skips "founded 10 years ago" and preferred/bonus asks.
    """
    text = description or ""
    found = []
    for m in YEARS_RE.finditer(text):
        before = text[max(0, m.start() - 40):m.start()]
        end = text.find("\n", m.end())
        line = text[text.rfind("\n", 0, m.start()) + 1: end if end != -1 else len(text)]
        heading = text[max(0, m.start() - 120):m.start()]
        if NOT_A_REQUIREMENT.search(before) or NICE_TO_HAVE.search(line) or \
                re.search(r"preferred qualifications|nice[- ]to[- ]haves?:?\s*$|bonus points", heading, re.I):
            continue
        if not (REQUIREMENT_AFTER.match(text[m.end():m.end() + 80]) or
                re.search(r"experience|required|minimum|at least", before, re.I)):
            continue
        n = int(m.group(1))
        if 0 < n <= 15:
            found.append(n)
    return max(found) if found else None
