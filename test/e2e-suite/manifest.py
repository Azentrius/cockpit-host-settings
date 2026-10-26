#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The version manifest of an E2E-1 run (workflow e2e-suite): for each candidate the repository,
the commit asked for and the one checked out with its subject, and the package built from it
(name, version, file, SHA-256); then the commit and run of the test itself. Fails when a checkout
is not the commit asked for, or when it did not build exactly one .deb.

    python3 test/e2e-suite/manifest.py OUT DIR=COMMIT [DIR=COMMIT ...]

Writes OUT/manifest.json and OUT/manifest.md, and prints the latter.
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


def main(argv):
    out = argv[0]
    os.makedirs(out, exist_ok=True)
    components, problems = [], []
    for arg in argv[1:]:
        d, _, want = arg.partition("=")
        url = git(d, "config", "--get", "remote.origin.url")
        repo = url.split("github.com/", 1)[-1]
        repo = repo[:-4] if repo.endswith(".git") else repo
        head = git(d, "rev-parse", "HEAD")
        c = {"repository": repo, "requested": want, "commit": head, "subject": git(d, "log", "-1", "--format=%s")}
        if head != want:
            problems.append("%s: checked out %s, not %s" % (repo, head, want))
        debs = sorted(glob.glob(os.path.join(d, "dist", "*.deb")))
        if len(debs) != 1:
            problems.append("%s: %d .deb built, not one" % (repo, len(debs)))
        else:
            c.update(package=field(debs[0], "Package"), version=field(debs[0], "Version"),
                     file=os.path.basename(debs[0]), sha256=sha256(debs[0]))
        components.append(c)
    doc = {"test": {"repository": os.environ.get("GITHUB_REPOSITORY"), "commit": os.environ.get("GITHUB_SHA"),
                    "run": os.environ.get("GITHUB_RUN_ID"), "event": os.environ.get("GITHUB_EVENT_NAME")},
           "components": components, "problems": problems}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    md = ["## E2E-1: version manifest", "", "| Repository | Commit | Package | Version |", "|---|---|---|---|"]
    md += ["| %s | `%s` %s | %s | %s |" % (c["repository"], c["commit"], c["subject"].replace("|", "\\|"),
                                         c.get("package", "-"), c.get("version", "-")) for c in components]
    md += ["", "Test: %s at `%s`, run %s (%s)." % (doc["test"]["repository"], doc["test"]["commit"], doc["test"]["run"],
                                                    doc["test"]["event"])]
    md += [""] + ["**%s**" % p for p in problems]
    with open(os.path.join(out, "manifest.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
