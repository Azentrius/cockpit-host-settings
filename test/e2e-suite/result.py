#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The result of an E2E-1 run (job suite of .github/workflows/suite.yml), in one place: the
combination from the version manifest, and what the core path (core.py) and the real Cockpit
(ui.py) found, with the scenarios that ran or were not supported. No credential is read or
written: only the files the test wrote to OUT.

    python3 test/e2e-suite/result.py OUT

Writes OUT/result.json and OUT/result.md, and prints the latter (for the job's summary). The
exit code is always 0: the steps before decide whether the job fails.
"""
import json
import os
import sys


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def part(out, name):
    checks = load(os.path.join(out, name, "assertions.json"), None)
    if checks is None:
        return {"ran": False}
    failed = [c for c in checks if "check" in c and not c["ok"]]
    return {"ran": True, "checks": sum(1 for c in checks if "check" in c), "failed": len(failed),
            "first_failure": ({k: failed[0].get(k) for k in ("step", "check", "detail")} if failed else None),
            "scenarios": load(os.path.join(out, name, "scenarios.json"), {})}


def main(argv):
    out = argv[0]
    manifest = load(os.path.join(out, "manifest.json"), {})
    parts = {"core": part(out, "core"), "ui": part(out, "ui")}
    passed = (not manifest.get("problems") and bool(manifest.get("components"))
              and all(p["ran"] and p["failed"] == 0 for p in parts.values()))
    doc = {"passed": passed,
           "combination": [{k: c.get(k) for k in ("repository", "source", "tag", "commit", "package", "version", "sha256")}
                           for c in manifest.get("components") or []],
           "suite": manifest.get("suite"), "required_scenarios": manifest.get("required_scenarios"),
           "run": manifest.get("run"), "problems": manifest.get("problems"), "parts": parts}
    with open(os.path.join(out, "result.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    md = ["## E2E-1: %s" % ("passed" if passed else "FAILED"), "",
          "| Repository | Source | Commit | Version |", "|---|---|---|---|"]
    md += ["| %s | %s | `%s` | %s |" % (c["repository"], " ".join(x for x in (c.get("source"), c.get("tag")) if x) or "-",
                                       c.get("commit"), c.get("version") or "-") for c in doc["combination"]]
    md += [""]
    for name, p in parts.items():
        if not p["ran"]:
            md.append("- %s: did not run" % name)
            continue
        sc = ", ".join("%s %s" % (k, v.get("result")) for k, v in sorted(p["scenarios"].items())) or "none"
        md.append("- %s: %d checks, %d failed; scenarios: %s" % (name, p["checks"], p["failed"], sc))
        if p["first_failure"]:
            f = p["first_failure"]
            md.append("  first failure%s: %s" % (" (step %s)" % f["step"] if f.get("step") is not None else "",
                                                 str(f.get("check")).replace("\n", " ")))
    with open(os.path.join(out, "result.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
