#!/usr/bin/env python3
"""Checks each award page in awards.json for changes and sends deadline reminders.
Uses only the Python standard library. Notifications are GitHub issues."""
import datetime, difflib, hashlib, json, os, re, subprocess, urllib.request
from html.parser import HTMLParser

TODAY = datetime.date.today()
DRY = os.environ.get("DRY_RUN") == "1"
OWNER = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
UA = "Mozilla/5.0 (compatible; AwardWatcher/1.0)"
SKIP = ("script", "style", "noscript", "svg")
BREAKS = ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "section")
os.makedirs("snapshots", exist_ok=True)


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        elif tag in BREAKS:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def fetch(url):
    """Return the visible text of a page (or a fingerprint for a PDF)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
    if data.startswith(b"%PDF"):
        return "PDF fingerprint: " + hashlib.sha256(data).hexdigest()
    parser = Text()
    parser.feed(data.decode(charset, "replace"))
    lines = [re.sub(r"\s+", " ", l).strip() for l in "".join(parser.out).splitlines()]
    return "\n".join(l for l in lines if l)


def notify(title, body):
    print("NOTIFY:", title)
    if DRY:
        return
    if OWNER:
        body = f"@{OWNER}\n\n{body}"
    try:
        subprocess.run(["gh", "issue", "create", "--title", title, "--body", body], check=True)
    except Exception as e:
        print("Could not open issue:", e)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


awards = json.loads(read("awards.json"))
try:
    state = json.loads(read("state.json"))
except FileNotFoundError:
    state = {}

for a in awards:
    aid, name, url = a["id"], a["name"], a.get("url", "")
    s = state.setdefault(aid, {})

    # 1. Deadline reminders at 60, 30 and 7 days (only the closest one is sent)
    dl = a.get("deadline")
    if dl:
        if s.get("deadline") != dl:
            s["deadline"], s["reminded"] = dl, []
        left = (datetime.date.fromisoformat(dl) - TODAY).days
        due = [t for t in (60, 30, 7) if 0 <= left <= t]
        if due and min(due) not in s["reminded"]:
            notify(f"Deadline in {left} days: {name}",
                   f"{name} is due {dl}.\n{url}\n\n{a.get('notes', '')}")
        s["reminded"] = sorted(set(s["reminded"]) | set(due))

    # 2. Page change check
    if not url:
        continue
    try:
        text = fetch(url)
        s["fails"] = 0
    except Exception as e:
        s["fails"] = s.get("fails", 0) + 1
        print(f"{name}: fetch failed ({e})")
        if s["fails"] == 3:
            notify(f"Can't reach page: {name}",
                   f"{url} failed 3 checks in a row ({e}). It may have moved or be blocking automated checks.")
        continue

    path = f"snapshots/{aid}.txt"
    s["checked"] = str(TODAY)
    if not os.path.exists(path):
        print(f"{name}: first check, baseline saved")
    elif read(path).strip() != text.strip():
        diff = [l for l in difflib.unified_diff(read(path).splitlines(), text.splitlines(), lineterm="", n=0)
                if not l.startswith(("---", "+++", "@@"))]
        shown = "\n".join(diff[:60]) + ("\n..." if len(diff) > 60 else "")
        notify(f"Page changed: {name}",
               f"The page changed since the last check.\n{url}\n\n```diff\n{shown}\n```\n"
               "Read the page, update your award details, then close this issue.")
        s["changed"] = str(TODAY)
    else:
        print(f"{name}: no change")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

with open("state.json", "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2, sort_keys=True)
