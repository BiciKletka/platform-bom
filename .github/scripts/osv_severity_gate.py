#!/usr/bin/env python3
"""Fail a pull request on High/Critical findings from osv-scanner, and police the allowlist.

Usage: osv_severity_gate.py RESULTS.json [--config osv-scanner.toml] [--threshold 7.0]
                           [--sbom target/bom.json --min-components N]

osv-scanner reports every known vulnerability; this gate fails only on groups whose highest CVSS
score is at or above the threshold (7.0 = High), or whose severity is unknown (treated as
failing, because "unknown" is not "low"). Vulnerabilities listed in the allowlist (osv-scanner.toml)
are already removed from RESULTS.json by osv-scanner itself, until their ignoreUntil date.

The allowlist is held to these rules, checked here: [[IgnoredVulns]] is the only suppression route
(any other key of osv-scanner.toml, notably [[PackageOverrides]] with ignore = true, would hide
findings without a reason or an expiry, so it fails the gate), every entry has a reason, every entry
has an unquoted ignoreUntil date, and no entry is valid for more than MAX_ALLOWLIST_DAYS from today
(so an unfixable finding is re-reviewed on a schedule instead of forgotten). An expired entry fails
the gate too: it either gets fixed or gets re-reviewed.

With --sbom the gate also reads the CycloneDX file osv-scanner scanned: it fails when the SBOM lists
fewer than --min-components components (a build change that silently dropped dependencies would
otherwise yield a clean scan of almost nothing), and it prints findings as group:name@version
(osv-scanner's results carry the artifact name only).

Exit codes: 0 pass, 1 findings, allowlist violations or a too small SBOM, 2 the results file or SBOM is missing
or unreadable.
"""
import argparse
import datetime
import json
import sys
import tomllib

MAX_ALLOWLIST_DAYS = 90
ENTRY_KEYS = {"id", "ignoreUntil", "reason"}


def allowlist_problems(config_path, today):
    try:
        with open(config_path, "rb") as handle:
            config = tomllib.load(handle)
    except FileNotFoundError:
        return []
    except tomllib.TOMLDecodeError as error:
        return [f"cannot parse {config_path}: {error}"]
    problems = []
    for key in config:
        if key != "IgnoredVulns":
            problems.append(f"{config_path} uses [{key}]: only [[IgnoredVulns]] entries (with reason and "
                            "ignoreUntil) may suppress a finding; osv-scanner would hide it without either")
    entries = config.get("IgnoredVulns", [])
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        problems.append(f"{config_path}: IgnoredVulns must be an array of tables ([[IgnoredVulns]])")
        return problems
    for entry in entries:
        ident = entry.get("id", "<no id>")
        for key in sorted(set(entry) - ENTRY_KEYS):
            problems.append(f"allowlist entry {ident} has the unsupported key {key}")
        if not str(entry.get("reason", "")).strip():
            problems.append(f"allowlist entry {ident} has no reason")
        until = entry.get("ignoreUntil")
        if until is None:
            problems.append(f"allowlist entry {ident} has no ignoreUntil date")
            continue
        if isinstance(until, datetime.datetime):
            until = until.date()
        if not isinstance(until, datetime.date):
            problems.append(f"allowlist entry {ident}: ignoreUntil must be an unquoted TOML date "
                            f"(ignoreUntil = 2027-01-05), got {until!r}")
            continue
        if until < today:
            problems.append(f"allowlist entry {ident} expired on {until}: fix the dependency or re-review it")
        elif until > today + datetime.timedelta(days=MAX_ALLOWLIST_DAYS):
            problems.append(f"allowlist entry {ident} runs until {until}, more than {MAX_ALLOWLIST_DAYS} days out")
    return problems


def read_sbom(path):
    """(component count, {(name, version): "group:name"}) of a CycloneDX JSON file."""
    with open(path, encoding="utf-8") as handle:
        components = json.load(handle).get("components", [])
    coordinates = {}
    for component in components:
        name = component.get("name", "")
        group = component.get("group")
        coordinates[(name, component.get("version", ""))] = f"{group}:{name}" if group else name
    return len(components), coordinates


def findings(results, threshold, coordinates=None):
    found = []
    for result in results.get("results", []):
        for package in result.get("packages", []):
            key = (package["package"]["name"], package["package"]["version"])
            name = f"{(coordinates or {}).get(key, key[0])}@{key[1]}"
            for group in package.get("groups", []):
                raw = group.get("max_severity", "")
                try:
                    score = float(raw)
                except (TypeError, ValueError):
                    score = None
                if score is None or score >= threshold:
                    found.append((name, ", ".join(group.get("ids", [])), raw or "unknown"))
    return found


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("results")
    parser.add_argument("--config", default="osv-scanner.toml")
    parser.add_argument("--threshold", type=float, default=7.0)
    parser.add_argument("--sbom", help="the CycloneDX file that was scanned")
    parser.add_argument("--min-components", type=int, help="fail when the SBOM lists fewer components")
    args = parser.parse_args(argv)
    if args.min_components is not None and not args.sbom:
        parser.error("--min-components needs --sbom")

    try:
        with open(args.results, encoding="utf-8") as handle:
            results = json.load(handle)
    except (OSError, ValueError) as error:
        print(f"cannot read the osv-scanner results ({args.results}): {error}", file=sys.stderr)
        return 2

    problems = allowlist_problems(args.config, datetime.date.today())
    coordinates = None
    if args.sbom:
        try:
            count, coordinates = read_sbom(args.sbom)
        except (OSError, ValueError) as error:
            print(f"cannot read the SBOM ({args.sbom}): {error}", file=sys.stderr)
            return 2
        if args.min_components is not None and count < args.min_components:
            problems.append(f"the SBOM lists {count} components, fewer than the {args.min_components} expected: "
                            "the dependency resolution changed, check the build before trusting this scan")
    for name, ids, severity in findings(results, args.threshold, coordinates):
        problems.append(f"{name}: {ids} (severity {severity})")

    if problems:
        print(f"Dependency gate failed (threshold CVSS {args.threshold}):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Dependency gate passed: no High/Critical findings, allowlist in order.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
