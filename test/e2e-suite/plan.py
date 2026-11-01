#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The plan of a run of the shared suite (E2E-1, job plan of .github/workflows/suite.yml): the
commit of each product to build, and whether a pull request needs the suite at all. It is made
once, before anything is built; the job suite takes the commits from here and never asks for a
branch.

    python3 plan.py SUITE_MANIFEST [CHANGED_FILES]

Environment: CALLER (the repository the workflow runs for, github.repository), EVENT
(github.event_name), HEAD (the candidate: the head commit of a pull request, else github.sha),
and SECURITY_MONITOR, HOST_SETTINGS, STATUS_BOARD (commits given as inputs, or empty).

Each product, in this order:
  1. the commit given as input (source "input": an explicit combination);
  2. HEAD when CALLER is the product's repository (source "candidate");
  3. the commit of the released combination in SUITE_MANIFEST (source "manifest").

A pull request whose changed files (CHANGED_FILES, one per line) are all text (TEXT below) does
not need the suite. Every other event always does, and so does a pull request whose list is
missing or empty. Writes run, why, security_monitor, host_settings, status_board and plan (the
whole plan as JSON) to GITHUB_OUTPUT when set, and the plan to standard output. Exit 1 for an
input or HEAD that is not a full commit id, or for a caller that is none of the three products
without an input for each.
"""
import fnmatch
import json
import os
import re
import sys

FULL = re.compile(r"[0-9a-f]{40}")
ORDER = (("security-monitor", "SECURITY_MONITOR", "security_monitor"),
         ("host-settings", "HOST_SETTINGS", "host_settings"),
         ("status-board", "STATUS_BOARD", "status_board"))
# What a pull request may change without the suite: text and pictures. The status contract is
# text that the products implement, so a change of it runs the suite.
TEXT = ("*.md", "docs/img/*", "LICENSE", "NOTICE")
NOT_TEXT = ("docs/STATUS-CONTRACT.md",)


def text_only(path):
    return not any(fnmatch.fnmatchcase(path, p) for p in NOT_TEXT) and any(fnmatch.fnmatchcase(path, p) for p in TEXT)


def changed_files(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def main(argv):
    with open(argv[0], encoding="utf-8") as f:
        manifest = json.load(f)
    caller, event, head = os.environ.get("CALLER", ""), os.environ.get("EVENT", ""), os.environ.get("HEAD", "")
    problems, components = [], []
    for key, env, _ in ORDER:
        m = manifest["components"][key]
        given = os.environ.get(env, "").strip()
        if given:
            c = {"key": key, "repository": m["repository"], "commit": given, "source": "input"}
        elif m["repository"] == caller:
            c = {"key": key, "repository": m["repository"], "commit": head, "source": "candidate"}
        else:
            c = {"key": key, "repository": m["repository"], "commit": m["commit"], "source": "manifest",
                 "tag": m.get("tag")}
        if not FULL.fullmatch(c["commit"] or ""):
            problems.append("%s: not a full commit id (%s): %r" % (key, c["source"], c["commit"]))
        components.append(c)
    if caller not in [c["repository"] for c in components] and not all(c["source"] == "input" for c in components):
        problems.append("the calling repository %s is none of the three products: give a commit for each" % caller)

    files = changed_files(argv[1] if len(argv) > 1 else "") if event == "pull_request" else None
    if event != "pull_request":
        run, why = True, "a %s run always tests its combination" % (event or "manual")
    elif not files:
        run, why = True, "the files the pull request changes are not known"
    else:
        other = [p for p in files if not text_only(p)]
        run = bool(other)
        why = ("it changes %d file(s) beyond text, %s" % (len(other), ", ".join(other[:5]) + (" ..." if len(other) > 5 else ""))
               if run else "the pull request changes only text: " + ", ".join(files[:10]) + (" ..." if len(files) > 10 else ""))

    plan = {"caller": caller, "event": event, "head": head,
            "candidate": "the head commit of the pull request" if event == "pull_request" else "the commit of the run",
            "combination": manifest.get("combination"), "required_scenarios": manifest.get("required_scenarios") or [],
            "components": components, "run": run, "why": why, "problems": problems}
    print(json.dumps(plan, indent=1, ensure_ascii=False))
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("run=%s\n" % ("true" if run and not problems else "false"))
            f.write("why=%s\n" % why.replace("\n", " "))
            for (_, _, name), c in zip(ORDER, components):
                f.write("%s=%s\n" % (name, c["commit"]))
            f.write("plan=%s\n" % json.dumps(plan, ensure_ascii=False, separators=(",", ":")))
    for p in problems:
        print("::error title=E2E-1 plan::" + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
