#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The version manifest of an E2E-1 run (job suite of .github/workflows/suite.yml): for each
product where it comes from (the plan: candidate, input or the released combination), the
commit the plan named, the one checked out with its subject, and the package built from it
(name, version, file, SHA-256, and for a released commit whether the .deb has the bytes of the
release); then the commit of the test and its released combination, and the run. Fails when a
checkout is not the commit the plan named, or when it did not build exactly one .deb.

    python3 test/e2e-suite/manifest.py OUT DIR=COMMIT [DIR=COMMIT ...]

Environment: PLAN (the plan as JSON, from test/e2e-suite/plan.py), SUITE_SHA (the commit of
Azentrius/cockpit-host-settings the test comes from), SUITE_MANIFEST (its released combination,
test/e2e-suite/suite-manifest.json). Writes OUT/manifest.json and OUT/manifest.md, and prints the
latter.
"""
import glob
import hashlib
import json
import os
import subprocess
import sys


def git(d, *args):
    return subprocess.run(["git", "-C", d] + list(args), capture_output=True, text=True, check=True).stdout.strip()


def field(deb, name):
    return subprocess.run(["dpkg-deb", "-f", deb, name], capture_output=True, text=True, check=True).stdout.strip()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load(path):
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    out = argv[0]
    os.makedirs(out, exist_ok=True)
    plan = json.loads(os.environ.get("PLAN") or "{}")
    released = load(os.environ.get("SUITE_MANIFEST", ""))
    planned = {c["repository"]: c for c in plan.get("components") or []}
    rel = {c["repository"]: c for c in (released.get("components") or {}).values()}
    components, problems = [], []
    for arg in argv[1:]:
        d, _, want = arg.partition("=")
        url = git(d, "config", "--get", "remote.origin.url")
        repo = url.split("github.com/", 1)[-1]
        repo = repo[:-4] if repo.endswith(".git") else repo
        head = git(d, "rev-parse", "HEAD")
        p = planned.get(repo) or {}
        c = {"repository": repo, "source": p.get("source"), "requested": want, "commit": head,
             "subject": git(d, "log", "-1", "--format=%s")}
        if p.get("tag"):
            c["tag"] = p["tag"]
        if head != want:
            problems.append("%s: checked out %s, not %s" % (repo, head, want))
        if p.get("commit") != want:
            problems.append("%s: the plan named %s, the job was given %s" % (repo, p.get("commit"), want))
        debs = sorted(glob.glob(os.path.join(d, "dist", "*.deb")))
        if len(debs) != 1:
            problems.append("%s: %d .deb built, not one" % (repo, len(debs)))
        else:
            c.update(package=field(debs[0], "Package"), version=field(debs[0], "Version"),
                     file=os.path.basename(debs[0]), sha256=sha256(debs[0]))
            r = rel.get(repo) or {}
            pkg = r.get("package") or {}
            if r.get("commit") == head and pkg.get("sha256"):
                # Informational: the packages build reproducibly from the commit's time, so the
                # .deb of a released commit should have the release's bytes.
                c["release"] = {"tag": r.get("tag"), "sha256": pkg["sha256"], "same_bytes": pkg["sha256"] == c["sha256"]}
        components.append(c)
    if sorted(planned) != sorted(c["repository"] for c in components):
        problems.append("the plan names %s, the job built %s" % (sorted(planned), sorted(c["repository"] for c in components)))
    doc = {"suite": {"repository": "Azentrius/cockpit-host-settings", "commit": os.environ.get("SUITE_SHA"),
                     "manifest": "test/e2e-suite/suite-manifest.json", "combination": released.get("combination")},
           "required_scenarios": released.get("required_scenarios") or [],
           "plan": {k: plan.get(k) for k in ("caller", "event", "head", "candidate", "why")},
           "run": {"repository": os.environ.get("GITHUB_REPOSITORY"), "commit": os.environ.get("GITHUB_SHA"),
                    "workflow": os.environ.get("GITHUB_WORKFLOW_REF"), "run": os.environ.get("GITHUB_RUN_ID"),
                    "attempt": os.environ.get("GITHUB_RUN_ATTEMPT"), "event": os.environ.get("GITHUB_EVENT_NAME")},
           "components": components, "problems": problems}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")

    def same(c):
        r = c.get("release")
        return "" if not r else " (the bytes of %s)" % r["tag"] if r["same_bytes"] else " (**not** the bytes of %s)" % r["tag"]

    md = ["## E2E-1: version manifest", "", "| Repository | Source | Commit | Package | Version |", "|---|---|---|---|---|"]
    md += ["| %s | %s%s | `%s` %s | %s | %s%s |" % (c["repository"], c.get("source") or "-", " " + c["tag"] if c.get("tag") else "",
                                                 c["commit"], c["subject"].replace("|", "\\|"), c.get("package", "-"),
                                                 c.get("version", "-"), same(c)) for c in components]
    md += ["", "Test and released combination: %s at `%s` (%s)." % (doc["suite"]["repository"], doc["suite"]["commit"],
                                                                   doc["suite"]["combination"])]
    md += ["Candidate: %s (%s, %s run %s)." % (plan.get("candidate"), doc["run"]["repository"], doc["run"]["event"],
                                               doc["run"]["run"])]
    md += [""] + ["**%s**" % p for p in problems]
    with open(os.path.join(out, "manifest.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))
    for c in components:
        if c.get("release") and not c["release"]["same_bytes"]:
            print("::warning title=E2E-1 manifest::%s: the .deb built from %s does not have the bytes of %s" %
                  (c["repository"], c["commit"], c["release"]["tag"]))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
