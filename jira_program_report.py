#!/usr/bin/env python3
"""Build an HTML program report from one Jira initiative key.

    export JIRA_USER=your.name
    python3 jira_program_report.py ABC-123

Reads the initiative, its epics, and the stories/tasks under them through the
same /rest/api/2/search endpoint as your curl, then writes one self-contained
HTML file (report_template.html with the data embedded). Standard library only.

Auth (checked in this order):
  JIRA_TOKEN      personal access token, sent as a Bearer token
  JIRA_USER + JIRA_PASSWORD   basic auth (prompted for if the password is unset)
"""
import argparse
import base64
import getpass
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_EXCLUDED = "Rejected,Won't Do,Wont Do,Duplicate,Cancelled,Canceled,Obsolete"
REVIEW_RE = re.compile(r"review|test|verif|\bqa\b|uat|validat|accept", re.I)


class JiraError(Exception):
    pass


class Jira:
    def __init__(self, base, user=None, password=None, token=None, insecure=False, debug=False):
        self.base = base.rstrip("/")
        self.debug = debug
        self.headers = {"Accept": "application/json"}
        if token:
            self.headers["Authorization"] = "Bearer " + token
        else:
            raw = f"{user}:{password}".encode()
            self.headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        self.ctx = ssl._create_unverified_context() if insecure else None

    def get(self, path, params=None):
        url = f"{self.base}/rest/api/2{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        if self.debug:
            print("GET", url, file=sys.stderr)
        for attempt in range(4):
            req = urllib.request.Request(url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=90, context=self.ctx) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:400]
                if e.code in (429, 502, 503, 504) and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                if e.code in (401, 403):
                    raise SystemExit(f"Jira rejected the credentials (HTTP {e.code}). {body}")
                raise JiraError(f"HTTP {e.code}: {body}")
            except urllib.error.URLError as e:
                raise SystemExit(f"Cannot reach {self.base}: {e.reason}")

    def search(self, jql, fields):
        out, start = [], 0
        while True:
            page = self.get("/search", {"jql": jql, "fields": fields, "maxResults": 100, "startAt": start})
            batch = page.get("issues", [])
            out.extend(batch)
            start += len(batch)
            if not batch or start >= page.get("total", 0):
                return out


def find_field(fields, *names):
    wanted = [n.lower() for n in names]
    for f in fields:
        if f.get("name", "").lower() in wanted:
            return f["id"]
    return None


def bucket(fields):
    st = fields.get("status") or {}
    cat = (st.get("statusCategory") or {}).get("key", "")
    name = st.get("name", "")
    if cat == "done":
        return "Done"
    if cat == "new":
        return "To Do"
    return "In Review" if REVIEW_RE.search(name) else "In Progress"


def pick_version(fix_versions):
    """Earliest unreleased version, else the latest released one."""
    if not fix_versions:
        return None
    unreleased = [v for v in fix_versions if not v.get("released")]
    if unreleased:
        return sorted(unreleased, key=lambda v: v.get("releaseDate") or "9999")[0]
    return sorted(fix_versions, key=lambda v: v.get("releaseDate") or "")[-1]


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def build_data(jira, key, base_url, epic_field=None, points_field=None, excluded=DEFAULT_EXCLUDED, log=print):
    excluded_names = [s.strip().lower() for s in excluded.split(",") if s.strip()]
    all_fields = jira.get("/field")
    epic_field = epic_field or find_field(all_fields, "Epic Link")
    points_field = points_field or find_field(all_fields, "Story Points", "Story point estimate")
    log(f"Fields: epic link={epic_field}, story points={points_field}")

    want = ["summary", "status", "priority", "issuetype", "assignee", "fixVersions", "resolution", "parent"]
    want += [f for f in (epic_field, points_field) if f]
    fields = ",".join(want)

    init = jira.get(f"/issue/{key}", {"fields": "summary,status,issuetype"})
    init_info = {
        "key": init["key"],
        "summary": init["fields"]["summary"],
        "status": (init["fields"].get("status") or {}).get("name", ""),
    }
    log(f"Initiative: {init_info['key']} {init_info['summary']}")

    pool = {}  # key -> raw issue

    def absorb(issues):
        for i in issues:
            if i["key"] != init_info["key"]:
                pool[i["key"]] = i

    def is_epic(i):
        return (i["fields"].get("issuetype") or {}).get("name", "").lower() == "epic"

    strategy = None
    attempts = [
        ("Advanced Roadmaps hierarchy", f'issue in portfolioChildIssuesOf("{key}")'),
        ('"Parent Link"', f'"Parent Link" = "{key}"'),
        ("parent field", f'parent = "{key}"'),
        ("issue links", f'issue in linkedIssues("{key}")'),
    ]
    for label, jql in attempts:
        try:
            found = jira.search(jql, fields)
        except JiraError as e:
            log(f"  {label}: not available ({str(e)[:80]})")
            continue
        log(f"  {label}: {len(found)} issues")
        if any(is_epic(i) for i in found):
            absorb(found)
            strategy = label
            break
        if found and strategy is None:
            absorb(found)
            strategy = label
            break
    if strategy is None:
        raise SystemExit(f"No epics or child issues found under {key}. Check the key and your access.")

    epic_keys = [k for k, i in pool.items() if is_epic(i)]
    log(f"Epics: {len(epic_keys)}")

    # children of each epic (also fills in stories the hierarchy query missed)
    for group in chunks(epic_keys, 40):
        keys = ",".join(group)
        got = False
        for jql in ([f'"Epic Link" in ({keys})'] if epic_field else []) + [f"parent in ({keys})"]:
            try:
                absorb(jira.search(jql, fields))
                got = True
            except JiraError:
                continue
        if not got:
            log("  warning: could not query epic children for " + keys)

    epic_info = {k: {"k": k, "n": pool[k]["fields"]["summary"],
                     "status": (pool[k]["fields"].get("status") or {}).get("name", "")} for k in epic_keys}

    issues, skipped = [], 0
    versions = {}
    for k, i in pool.items():
        f = i["fields"]
        itype = f.get("issuetype") or {}
        if is_epic(i) or itype.get("subtask") or itype.get("name", "").lower() == "initiative":
            continue
        raw_status = (f.get("status") or {}).get("name", "")
        if raw_status.lower() in excluded_names:
            skipped += 1
            continue
        ep = f.get(epic_field) if epic_field else None
        if not ep:
            p = (f.get("parent") or {}).get("key")
            ep = p if p in epic_info else None
        if ep not in epic_info:
            ep = "NONE"
        fv = pick_version(f.get("fixVersions"))
        vname = fv["name"] if fv else None
        if fv:
            v = versions.setdefault(vname, {"n": vname, "date": fv.get("releaseDate"), "released": True})
            if fv.get("releaseDate") and (not v["date"] or fv["releaseDate"] < v["date"]):
                v["date"] = fv["releaseDate"]
            v["released"] = v["released"] and bool(fv.get("released"))
        pts = f.get(points_field) if points_field else None
        issues.append({
            "key": k, "summary": f.get("summary", ""), "type": itype.get("name", "Issue"),
            "epic": ep, "status": bucket(f), "rawStatus": raw_status,
            "prio": (f.get("priority") or {}).get("name", "None"),
            "who": (f.get("assignee") or {}).get("displayName") or (f.get("assignee") or {}).get("name") or "Unassigned",
            "pts": (int(pts) if float(pts).is_integer() else float(pts)) if isinstance(pts, (int, float)) else 0,
            "ver": vname or "none",
        })

    ordered = sorted(versions.values(), key=lambda v: (v["date"] is None, v["date"] or "", v["n"]))
    ver_list = []
    for n, v in enumerate(ordered, 1):
        ver_list.append({"id": f"fv{n}", "n": v["n"], "date": v["date"],
                         "state": "Released" if v["released"] else "Unreleased"})
    ids = {v["n"]: v["id"] for v in ver_list}
    for i in issues:
        i["ver"] = ids.get(i["ver"], "none")
    if any(i["ver"] == "none" for i in issues):
        ver_list.append({"id": "none", "n": "No fix version", "date": None, "state": "Unplanned"})

    epics = [epic_info[k] for k in epic_keys if any(i["epic"] == k for i in issues)]
    epics += [epic_info[k] for k in epic_keys if k not in {e["k"] for e in epics}]
    if any(i["epic"] == "NONE" for i in issues):
        epics.append({"k": "NONE", "n": "No epic", "status": ""})
    epics = [e for e in epics]

    try:
        priorities = [p["name"] for p in jira.get("/priority")]
    except JiraError:
        priorities = []

    log(f"Issues: {len(issues)} counted, {skipped} excluded by status; versions: {len(ver_list)}")
    return {
        "initiative": init_info, "base": base_url.rstrip("/"),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "strategy": strategy, "issues": issues, "epics": epics, "versions": ver_list,
        "priorities": priorities, "hasPoints": any(i["pts"] for i in issues),
        "excluded": skipped, "excludedStatuses": [s.strip() for s in excluded.split(",") if s.strip()],
    }


def render(data, template_path):
    html = Path(template_path).read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=True).replace("</", "<\\/")
    return html.replace("__REPORT_DATA__", payload)


def main():
    ap = argparse.ArgumentParser(description="HTML program report for one Jira initiative")
    ap.add_argument("initiative", help="initiative issue key, e.g. ABC-123")
    ap.add_argument("--base-url", default=os.environ.get("JIRA_BASE_URL", "https://jira.verizon.com"))
    ap.add_argument("--user", default=os.environ.get("JIRA_USER"))
    ap.add_argument("-o", "--out", help="output file (default: <KEY>-report.html)")
    ap.add_argument("--epic-link-field", help="custom field id for Epic Link, e.g. customfield_10014")
    ap.add_argument("--points-field", help="custom field id for Story Points")
    ap.add_argument("--exclude-status", default=DEFAULT_EXCLUDED,
                    help="comma-separated statuses not counted (default: %(default)s)")
    ap.add_argument("--insecure", action="store_true", help="skip TLS certificate verification")
    ap.add_argument("--debug", action="store_true", help="print each request URL")
    a = ap.parse_args()

    token = os.environ.get("JIRA_TOKEN")
    password = os.environ.get("JIRA_PASSWORD")
    if not token:
        if not a.user:
            sys.exit("Set JIRA_USER (or --user), or set JIRA_TOKEN for a personal access token.")
        if not password:
            password = getpass.getpass(f"Jira password for {a.user}: ")
    jira = Jira(a.base_url, a.user, password, token, a.insecure, a.debug)
    data = build_data(jira, a.initiative.strip().upper(), a.base_url, a.epic_link_field, a.points_field,
                      a.exclude_status, log=lambda m: print(m, file=sys.stderr))
    template = Path(__file__).with_name("report_template.html")
    out = Path(a.out or f"{data['initiative']['key']}-report.html")
    out.write_text(render(data, template), encoding="utf-8")
    print(f"Wrote {out.resolve()}")


if __name__ == "__main__":
    main()
