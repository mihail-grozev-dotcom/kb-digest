#!/usr/bin/env python3
"""Weekly digest of the latest edited/created Forsta Surveys Knowledge Base articles.

Sends only when it is Monday 10:xx in Europe/Sofia (so the workflow can fire at both
07:00 and 08:00 UTC and handle daylight saving automatically). Set FORCE=1 to bypass.
Set DRY_RUN=1 to print the email instead of sending. Stdlib only.
"""
import json, os, smtplib, sys, html
from datetime import datetime
from email.message import EmailMessage
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

COUNT = int(os.environ.get("COUNT", "15"))
API = ("https://forstasurveys.zendesk.com/api/v2/help_center/en-us/articles.json"
       "?sort_by=edited_at&sort_order=desc&per_page=%d" % COUNT)
SOFIA = ZoneInfo("Europe/Sofia")


def fetch():
    if os.environ.get("SAMPLE_FILE"):  # offline testing
        with open(os.environ["SAMPLE_FILE"]) as f:
            return json.load(f)["articles"]
    req = Request(API, headers={"User-Agent": "kb-digest/1.0"})
    with urlopen(req, timeout=20) as r:
        return json.load(r)["articles"]


def build(articles):
    rows_html, rows_txt = [], []
    for a in articles:
        if a.get("draft"):
            continue
        edited = datetime.strptime(a["edited_at"], "%Y-%m-%dT%H:%M:%SZ")
        tag = "New" if a["created_at"][:10] == a["edited_at"][:10] else "Updated"
        d = edited.strftime("%b %d, %Y")
        rows_html.append('<li><a href="%s">%s</a> <small>(%s, %s)</small></li>'
                         % (a["html_url"], html.escape(a["title"]), tag, d))
        rows_txt.append("- %s (%s, %s)\n  %s" % (a["title"], tag, d, a["html_url"]))
    body_html = ("<h3>Latest Knowledge Base articles</h3><ul>%s</ul>"
                 '<p><a href="https://forstasurveys.zendesk.com/hc/en-us">Open the Knowledge Base</a></p>'
                 % "".join(rows_html))
    return body_html, "Latest Knowledge Base articles\n\n" + "\n".join(rows_txt)


def main():
    now = datetime.now(SOFIA)
    if not os.environ.get("FORCE") and not (now.weekday() == 0 and now.hour == 10):
        print("Not Monday 10:xx in Sofia (%s) - skipping." % now.isoformat())
        return 0
    body_html, body_txt = build(fetch())
    msg = EmailMessage()
    msg["Subject"] = "Weekly KB digest - %s" % now.strftime("%b %d, %Y")
    msg["From"] = os.environ.get("MAIL_FROM", "kb-digest@example.com")
    msg["To"] = os.environ.get("MAIL_TO", "you@example.com")
    msg.set_content(body_txt)
    msg.add_alternative(body_html, subtype="html")
    if os.environ.get("DRY_RUN"):
        print(msg)
        return 0
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)
    print("Sent to", msg["To"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
