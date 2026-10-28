#!/usr/bin/env python3
"""Tracks what changed in each Forsta Surveys Knowledge Base article.

Every run: downloads all published articles, converts the body to plain text, compares it
with the copy saved in snapshots/<id>.txt and appends the differences to changes.json.
The first run only records a baseline (no changes are reported). Stdlib only.
Set SAMPLE_FILE=x.json to run against offline test data.
"""
import difflib, json, os, re, sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen

API = ("https://forstasurveys.zendesk.com/api/v2/help_center/en-us/articles.json"
       "?per_page=100&sort_by=edited_at&sort_order=desc&page=%d")
SNAP_DIR, CHANGES_FILE = "snapshots", "changes.json"
KEEP_DAYS, MAX_ITEMS, MAX_LEN, MAX_PAGES = 120, 8, 300, 40
BLOCK = {"p", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "div", "pre",
         "ul", "ol", "table", "blockquote", "section", "hr"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def to_lines(body):
    p = _Text()
    p.feed(body or "")
    p.close()
    lines = [re.sub(r"\s+", " ", l).strip() for l in "".join(p.parts).split("\n")]
    return [l for l in lines if l]


def short(s):
    return s if len(s) <= MAX_LEN else s[:MAX_LEN - 1] + "…"


def word_change(old, new, ctx=4):
    a, b = old.split(), new.split()
    ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
    if not ops:
        return None
    i1, i2 = min(o[1] for o in ops), max(o[2] for o in ops)
    j1, j2 = min(o[3] for o in ops), max(o[4] for o in ops)
    lo_a, hi_a = max(0, i1 - ctx), min(len(a), i2 + ctx)
    lo_b, hi_b = max(0, j1 - ctx), min(len(b), j2 + ctx)
    def frag(w, lo, hi):
        return ("… " if lo > 0 else "") + " ".join(w[lo:hi]) + (" …" if hi < len(w) else "")
    return short(frag(a, lo_a, hi_a)), short(frag(b, lo_b, hi_b))


def diff_lines(old, new):
    changes = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "insert":
            changes += [{"op": "added", "text": short(l)} for l in new[j1:j2]]
        elif tag == "delete":
            changes += [{"op": "removed", "text": short(l)} for l in old[i1:i2]]
        elif tag == "replace":
            olds, used = old[i1:i2], set()
            for nl in new[j1:j2]:
                best, score = None, 0.5
                for k, ol in enumerate(olds):
                    r = difflib.SequenceMatcher(None, ol, nl, autojunk=False).ratio()
                    if k not in used and r >= score:
                        best, score = k, r
                if best is None:
                    changes.append({"op": "added", "text": short(nl)})
                else:
                    used.add(best)
                    wc = word_change(olds[best], nl) if olds[best] != nl else None
                    if wc:  # identical lines (repeated text that merely moved) are not a change
                        changes.append({"op": "changed", "old": wc[0], "new": wc[1]})
            changes += [{"op": "removed", "text": short(ol)} for k, ol in enumerate(olds) if k not in used]
    return changes


def fetch_all():
    if os.environ.get("SAMPLE_FILE"):
        with open(os.environ["SAMPLE_FILE"]) as f:
            return json.load(f)["articles"]
    out = []
    for p in range(1, MAX_PAGES + 1):
        with urlopen(Request(API % p, headers={"User-Agent": "kb-track/1.0"}), timeout=60) as r:
            data = json.load(r)
        out += data["articles"]
        if not data.get("next_page"):
            break
    return out


def main():
    os.makedirs(SNAP_DIR, exist_ok=True)
    marker = os.path.join(SNAP_DIR, ".baseline")
    baseline = not os.path.exists(marker)
    try:
        with open(CHANGES_FILE) as f:
            entries = json.load(f)["entries"]
    except (OSError, ValueError, KeyError):
        entries = []
    now = datetime.now(timezone.utc)
    detected = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    n_new = n_changed = 0
    for a in fetch_all():
        path = os.path.join(SNAP_DIR, "%s.txt" % a["id"])
        if a.get("draft"):
            if os.path.exists(path):
                os.remove(path)
            continue
        lines = ["Title: " + a["title"]] + to_lines(a.get("body"))
        old = None
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                old = f.read().split("\n")
        if old == lines:
            continue
        if not baseline:
            entry = {"id": a["id"], "title": a["title"], "url": a["html_url"],
                     "edited_at": a["edited_at"], "detected_at": detected}
            if old is None:
                entry["kind"], entry["changes"] = "new", []
                n_new += 1
            else:
                ch = diff_lines(old, lines)
                if not ch:
                    ch = []
                entry["kind"] = "updated"
                entry["more"] = max(0, len(ch) - MAX_ITEMS)
                entry["changes"] = ch[:MAX_ITEMS]
                n_changed += 1
            entries.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = [e for e in entries if e["detected_at"] >= cutoff]
    text = json.dumps({"entries": entries}, ensure_ascii=False, indent=1) + "\n"
    try:
        with open(CHANGES_FILE, encoding="utf-8") as f:
            same = f.read() == text
    except OSError:
        same = False
    if not same:
        with open(CHANGES_FILE, "w", encoding="utf-8") as f:
            f.write(text)
    if baseline:
        open(marker, "w").close()
        print("Baseline recorded - future runs will report changes.")
    else:
        print("New articles: %d, changed articles: %d" % (n_new, n_changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
