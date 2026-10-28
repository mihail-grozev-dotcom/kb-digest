#!/usr/bin/env python3
"""Weekly digest of Forsta Surveys Knowledge Base articles created or edited LAST WEEK.

"Last week" = the previous full calendar week in Europe/Sofia: Monday 00:00 up to (not
including) the Monday of the current week. Sent only when it is Monday 10:xx in Sofia (the
workflow fires at 07:00 and 08:00 UTC to cover daylight saving). FORCE=1 bypasses the time
check, DRY_RUN=1 prints instead of sending, SAMPLE_FILE=x.json uses offline test data.
Stdlib only.
"""
import json, os, smtplib, sys, html
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SOFIA = ZoneInfo("Europe/Sofia")
API = ("https://forstasurveys.zendesk.com/api/v2/help_center/en-us/articles.json"
       "?sort_by=edited_at&sort_order=desc&per_page=100&page=%d")
MAX_PAGES = 10


def load_changes():
    try:
        with open(os.environ.get("CHANGES_FILE", "changes.json"), encoding="utf-8") as f:
            entries = json.load(f)["entries"]
    except (OSError, ValueError, KeyError):
        return {}
    by_id = {}
    for e in sorted(entries, key=lambda x: x["detected_at"]):
        by_id.setdefault(e["id"], []).append(e)
    return by_id


def details(a, start, end, by_id):
    """Merged change items for article a whose edit time falls in the window."""
    items, tracked = [], False
    for e in by_id.get(a["id"], []):
        if start <= parse(e["edited_at"]) < end:
            tracked = True
            items += e["changes"]
            if e.get("more"):
                items.append({"op": "more", "n": e["more"]})
    return tracked, items[:12]


def item_html(c):
    if c["op"] == "added":
        return "<li><b>Added:</b> %s</li>" % html.escape(c["text"])
    if c["op"] == "removed":
        return "<li><b>Removed:</b> <s>%s</s></li>" % html.escape(c["text"])
    if c["op"] == "changed":
        return "<li><b>Changed:</b> <s>%s</s> &rarr; %s</li>" % (html.escape(c["old"]), html.escape(c["new"]))
    return "<li><em>+%d more changes</em></li>" % c["n"]


def item_txt(c):
    if c["op"] == "added":
        return "    + Added: %s" % c["text"]
    if c["op"] == "removed":
        return "    - Removed: %s" % c["text"]
    if c["op"] == "changed":
        return "    ~ Changed: %s -> %s" % (c["old"], c["new"])
    return "    (+%d more changes)" % c["n"]


def parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def week_window(now):
    """Previous full Mon-Sun week in Sofia, returned as (start_utc, end_utc)."""
    this_monday = (now.astimezone(SOFIA) - timedelta(days=now.astimezone(SOFIA).weekday())
                   ).replace(hour=0, minute=0, second=0, microsecond=0)
    last_monday = this_monday - timedelta(days=7)
    return last_monday.astimezone(timezone.utc), this_monday.astimezone(timezone.utc)


def pages():
    if os.environ.get("SAMPLE_FILE"):  # offline testing
        with open(os.environ["SAMPLE_FILE"]) as f:
            yield json.load(f)["articles"]
        return
    for p in range(1, MAX_PAGES + 1):
        req = Request(API % p, headers={"User-Agent": "kb-digest/1.0"})
        with urlopen(req, timeout=20) as r:
            data = json.load(r)
        yield data["articles"]
        if not data.get("next_page"):
            return


def fetch(start, end):
    """Articles created or edited in [start, end). List is sorted by edited_at, newest first."""
    out = []
    for articles in pages():
        for a in articles:
            if a.get("draft"):
                continue
            edited, created = parse(a["edited_at"]), parse(a["created_at"])
            if start <= created < end or start <= edited < end:
                out.append(a)
            elif edited < start:  # everything after this is older still
                return out
    return out


def build(articles, start, end, by_id):
    rows_html, rows_txt = [], []
    for a in sorted(articles, key=lambda x: x["edited_at"], reverse=True):
        edited = parse(a["edited_at"]).astimezone(SOFIA)
        is_new = start <= parse(a["created_at"]) < end
        tag, d = ("New" if is_new else "Updated"), edited.strftime("%b %d, %Y")
        tracked, items = details(a, start, end, by_id)
        sub_h, sub_t = "", ""
        if items:
            sub_h = "<ul>%s</ul>" % "".join(item_html(c) for c in items)
            sub_t = "\n" + "\n".join(item_txt(c) for c in items)
        elif not is_new and not tracked:
            sub_h = "<ul><li><em>Change details not available.</em></li></ul>"
        rows_html.append('<li><a href="%s">%s</a> <small>(%s, %s)</small>%s</li>'
                         % (a["html_url"], html.escape(a["title"]), tag, d, sub_h))
        rows_txt.append("- %s (%s, %s)\n  %s%s" % (a["title"], tag, d, a["html_url"], sub_t))
    last_day = (end.astimezone(SOFIA) - timedelta(days=1))
    label = "%s - %s" % (start.astimezone(SOFIA).strftime("%b %d"), last_day.strftime("%b %d, %Y"))
    if not rows_html:
        empty = "No new or updated articles last week (%s)." % label
        return "<p>%s</p>" % empty, empty, label
    head = "New and updated Knowledge Base articles, %s (%d)" % (label, len(rows_html))
    body_html = ("<h3>%s</h3><ul>%s</ul>"
                 '<p><a href="https://forstasurveys.zendesk.com/hc/en-us">Open the Knowledge Base</a></p>'
                 % (html.escape(head), "".join(rows_html)))
    return body_html, head + "\n\n" + "\n".join(rows_txt), label


def main():
    now = datetime.fromisoformat(os.environ["NOW"]).astimezone(SOFIA) if os.environ.get("NOW") else datetime.now(SOFIA)
    if not os.environ.get("FORCE") and not (now.weekday() == 0 and now.hour == 10):
        print("Not Monday 10:xx in Sofia (%s) - skipping." % now.isoformat())
        return 0
    start, end = week_window(now)
    body_html, body_txt, label = build(fetch(start, end), start, end, load_changes())
    msg = EmailMessage()
    msg["Subject"] = "Weekly Knowledge Base Update for week %s" % label
    sender = os.environ.get("MAIL_FROM", "kb-digest@example.com")
    name = os.environ.get("MAIL_FROM_NAME", "").strip()
    msg["From"] = formataddr((name, sender)) if name else sender
    if os.environ.get("MAIL_REPLY_TO", "").strip():
        msg["Reply-To"] = os.environ["MAIL_REPLY_TO"].strip()
    msg["To"] = os.environ.get("MAIL_TO", "you@example.com")
    msg.set_content(body_txt)
    msg.add_alternative(body_html, subtype="html")
    if os.environ.get("DRY_RUN"):
        print(msg)
        return 0
    host = os.environ.get("SMTP_HOST", "").strip() or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT", "").strip() or "587")
    user, pw = os.environ.get("SMTP_USER", "").strip(), os.environ.get("SMTP_PASS", "")
    if not user or not pw:
        sys.exit("ERROR: the SMTP_USER and/or SMTP_PASS secret is missing or empty.")
    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            s.login(user, pw)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        sys.exit("ERROR: Gmail rejected the login (%s). Check SMTP_USER and use a Google APP PASSWORD in SMTP_PASS." % e.smtp_code)
    except (smtplib.SMTPException, OSError) as e:
        sys.exit("ERROR sending email via %s:%s - %s" % (host, port, e))
    print("Digest sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
