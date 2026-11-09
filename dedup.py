#!/usr/bin/env python3
# Trash extra Immich duplicates, or stack similar photos.
# Usage: see README.md
# Licensed under the MIT License. See LICENSE.

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta

# Group a series only when the largest file is at most 33% bigger than the smallest.
SIZE_GAP = 0.33

SUFFIX = re.compile(r"(?:\s*\(\d+\))+(?=\.[^.]+$)")
_ISO = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?")

BASE = ""
KEY = ""
APPLY = False


def norm(name):
    """Return the filename without a "(N)" suffix, lowercased."""
    return SUFFIX.sub("", name).lower()


def size(asset):
    """Return the file size in bytes, or None."""
    return (asset.get("exifInfo") or {}).get("fileSizeInByte")


def parse(value):
    """Parse a local capture date and time."""
    match = _ISO.match(value or "")
    if not match:
        sys.exit("Cannot parse local date: {}".format(value))
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    fraction = float("0." + match.group(7)) if match.group(7) else 0.0
    return datetime(year, month, day, hour, minute, second) + timedelta(seconds=fraction)


def date_ok(left, right):
    """Check that capture times match, allowing timezone offset."""
    delta = abs((parse(left["localDateTime"]) - parse(right["localDateTime"])).total_seconds())
    if delta <= 2:
        return True
    if delta > 14 * 3600 + 2:
        return False
    remainder = delta % 1800
    return remainder <= 2 or remainder >= 1798


def call(method, path, body=None):
    """Call the Immich API and return JSON."""
    request = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "x-api-key": KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        sys.exit("Immich API {} {} failed: HTTP {} {}".format(method, path, error.code, detail))
    except urllib.error.URLError as error:
        sys.exit("Immich API {} {} failed: {}".format(method, path, error.reason))


def by_size(items):
    """Sort largest file first; on equal size, prefer the earlier one."""
    return sorted(items, key=lambda asset: (-(size(asset) or 0), asset.get("fileCreatedAt", "")))


def size_gap_percent(items):
    """Return how many percent larger the biggest file is than the smallest."""
    sizes = [size(asset) for asset in items]
    largest, smallest = max(sizes), min(sizes)
    if not smallest:
        return 0.0
    return (largest - smallest) / smallest * 100


def size_close(items):
    """Check that series sizes differ by at most 33%."""
    sizes = [size(asset) for asset in items]
    if any(not value for value in sizes):
        return False
    return size_gap_percent(items) <= SIZE_GAP * 100


def order_stack(items):
    """Put the largest file first; sort the rest by capture time."""
    ranked = by_size(items)
    rest = sorted(ranked[1:], key=lambda asset: asset.get("localDateTime", ""))
    return [ranked[0], *rest]


def join_names(items):
    """Join filenames into one log line."""
    return " | ".join(asset["originalFileName"] for asset in items)


def show(keep, other, mark):
    """Print a keep/other pair."""
    diff = ""
    if norm(keep["originalFileName"]) != norm(other["originalFileName"]):
        diff = "  [different names]"
    print("KEEP {} ({} B) {} id={}".format(
        keep["originalFileName"], size(keep), keep["localDateTime"], keep["id"]))
    print("  {} {} ({} B) {} id={}{}".format(
        mark, other["originalFileName"], size(other), other["localDateTime"], other["id"], diff))


def show_stack(items):
    """Print a stack with the largest file on top."""
    top = items[0]
    print("TOP  {} ({} B) {} id={}".format(
        top["originalFileName"], size(top), top["localDateTime"], top["id"]))
    for asset in items[1:]:
        print("  +  {} ({} B) {} id={}".format(
            asset["originalFileName"], size(asset), asset["localDateTime"], asset["id"]))
    times = [parse(asset["localDateTime"]) for asset in items]
    span = (max(times) - min(times)).total_seconds()
    print("  series: {:.0f}s apart, size gap {:.1f}%".format(span, size_gap_percent(items)))


def collect_name_buckets(groups):
    """Group assets by owner and similar filename."""
    buckets = defaultdict(dict)
    for group in groups:
        for asset in group["assets"]:
            buckets[(asset["ownerId"], norm(asset["originalFileName"]))][asset["id"]] = asset
    return buckets


def scenario_name(groups):
    """Keep the largest same-named file when capture times match."""
    to_delete, review, skipped = [], [], []
    for assets in collect_name_buckets(groups).values():
        items = list(assets.values())
        if len(items) < 2:
            continue
        if any(size(asset) is None for asset in items):
            skipped.append([asset["originalFileName"] for asset in items])
            continue
        items = by_size(items)
        for asset in items[1:]:
            (to_delete if date_ok(items[0], asset) else review).append((items[0], asset))
    return to_delete, review, skipped


def created_day(asset):
    """Return the calendar day of the photo capture."""
    return parse(asset["localDateTime"]).date()


def much_lighter(keep, other):
    """Check that the file is more than 33% lighter than the largest one."""
    keep_size, other_size = size(keep), size(other)
    if not keep_size or not other_size:
        return False
    return (keep_size - other_size) / other_size > SIZE_GAP


def scenario_second(groups):
    """Keep the largest file shot in the same second within a duplicate group."""
    to_delete, review, skipped = [], [], []
    for group in groups:
        buckets = defaultdict(list)
        for asset in group["assets"]:
            key = (asset["ownerId"], parse(asset["localDateTime"]).replace(microsecond=0))
            buckets[key].append(asset)
        for items in buckets.values():
            if len(items) < 2:
                continue
            if any(size(asset) is None for asset in items):
                skipped.append([asset["originalFileName"] for asset in items])
                continue
            items = by_size(items)
            if len(items) > 2 or len({asset.get("type") for asset in items}) > 1:
                for asset in items[1:]:
                    review.append((items[0], asset))
            else:
                to_delete.append((items[0], items[1]))
    return to_delete, review, skipped


def scenario_compressed(groups):
    """Trash a compressed copy that shares the same creation date."""
    to_delete, review, skipped = [], [], []
    for group in groups:
        by_day = defaultdict(list)
        for asset in group["assets"]:
            by_day[(asset["ownerId"], created_day(asset))].append(asset)
        for items in by_day.values():
            if len(items) < 2:
                continue
            if any(size(asset) is None for asset in items):
                skipped.append([asset["originalFileName"] for asset in items])
                continue
            ranked = by_size(items)
            keep = ranked[0]
            for other in ranked[1:]:
                if other.get("type") != keep.get("type"):
                    review.append((keep, other))
                elif much_lighter(keep, other):
                    to_delete.append((keep, other))
    return to_delete, review, skipped


def scenario_stack(groups):
    """Build a stack from same-day duplicates when sizes are close."""
    stacks, skipped, notes = [], [], []
    used = set()
    for group in groups:
        assets = []
        seen = set()
        for asset in group["assets"]:
            if asset.get("stack") or asset["id"] in used or asset["id"] in seen:
                continue
            seen.add(asset["id"])
            assets.append(asset)
        if len(assets) < 2:
            continue
        buckets = defaultdict(list)
        for asset in assets:
            buckets[(asset["ownerId"], created_day(asset))].append(asset)
        for items in buckets.values():
            if len(items) < 2:
                continue
            if any(size(asset) is None for asset in items):
                skipped.append([asset["originalFileName"] for asset in items])
                notes.append("missing size: {}".format(join_names(items)))
                continue
            if not size_close(items):
                notes.append("size differs by {:.1f}%, over 33%: {}".format(
                    size_gap_percent(items), join_names(items)))
                continue
            ordered = order_stack(items)
            stacks.append(ordered)
            used.update(asset["id"] for asset in ordered)
    return stacks, skipped, notes


def confirm(prompt):
    """Return True only if the exact answer is yes."""
    try:
        answer = input(prompt)
    except EOFError:
        answer = ""
    return answer.strip() == "yes"


def report_trash(to_delete, review, skipped):
    """Print the delete plan and, on confirm, move files to trash."""
    print("=== DELETE ===")
    for keep, other in to_delete:
        show(keep, other, "DEL")
    print("\n=== REVIEW (not deleted) ===")
    for keep, other in review:
        show(keep, other, "???")
    print("\n=== SKIPPED (missing size) ===")
    for names in skipped[:50]:
        print(" ", ", ".join(names))
    print("\nTo delete: {}, to review: {}, skipped: {}".format(
        len(to_delete), len(review), len(skipped)))
    if not APPLY:
        print("Dry-run: nothing was deleted. Add --apply to delete.")
        return False
    if not to_delete:
        print("Nothing to delete.")
        return False
    ids = list({other["id"] for _, other in to_delete})
    if not confirm("Move {} files to trash? [yes/N] ".format(len(ids))):
        print("Cancelled.")
        return True
    for offset in range(0, len(ids), 100):
        call("DELETE", "/assets", {"ids": ids[offset:offset + 100], "force": False})
        print("  sent {}/{}".format(min(offset + 100, len(ids)), len(ids)))
    print("Done: moved to trash", len(ids))
    return False


def report_stacks(stacks, skipped, notes):
    """Print the stack plan and, on confirm, create the stacks."""
    print("=== STACK ===")
    for items in stacks:
        show_stack(items)
        print()
    print("=== SKIPPED (missing size) ===")
    for names in skipped[:50]:
        print(" ", ", ".join(names))
    print("\n=== NOT STACKED ===")
    for line in notes[:40]:
        print(" ", line)
    print("\nTo stack: {}, not stacked: {}, skipped: {}".format(
        len(stacks), len(notes), len(skipped)))
    if not APPLY:
        print("Dry-run: nothing was stacked. Add --apply to create stacks.")
        return False
    if not stacks:
        print("Nothing to stack.")
        return False
    if not confirm("Create {} stacks? [yes/N] ".format(len(stacks))):
        print("Cancelled.")
        return True
    for index, items in enumerate(stacks, 1):
        call("POST", "/stacks", {"assetIds": [asset["id"] for asset in items]})
        print("  stacked {}/{}".format(index, len(stacks)))
    print("Done: created", len(stacks), "stacks")
    return False


def scenario_from_args(argv):
    """Read a single scenario number if it was passed at launch."""
    if "--scenario" not in argv:
        return None
    index = argv.index("--scenario")
    if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
        sys.exit("--scenario must be 1, 2, 3, or 4.")
    scenario = argv[index + 1]
    if scenario not in ("1", "2", "3", "4"):
        sys.exit("--scenario must be 1, 2, 3, or 4.")
    return scenario


def drop_assets(groups, banned):
    """Remove already handled files from later scenarios."""
    kept = []
    for group in groups:
        assets = [asset for asset in group["assets"] if asset["id"] not in banned]
        if assets:
            kept.append({**group, "assets": assets})
    return kept


def delete_ids(pairs):
    """Collect ids of files the scenario chose to remove."""
    return {other["id"] for _, other in pairs}


def stack_ids(stacks):
    """Collect ids of files that were placed into stacks."""
    return {asset["id"] for items in stacks for asset in items}


def run_scenario(groups, scenario):
    """Print one scenario and apply it when confirmed."""
    print("=== SCENARIO {} ===".format(scenario))
    if scenario == "3":
        stacks, skipped, notes = scenario_stack(groups)
        cancelled = report_stacks(stacks, skipped, notes)
        return cancelled, stack_ids(stacks)
    if scenario == "4":
        to_delete, review, skipped = scenario_compressed(groups)
    elif scenario == "1":
        to_delete, review, skipped = scenario_name(groups)
    else:
        to_delete, review, skipped = scenario_second(groups)
    cancelled = report_trash(to_delete, review, skipped)
    return cancelled, delete_ids(to_delete)


def run_all(groups, include_group):
    """Run scenarios 1, 2, 3, and 4 one after another."""
    banned = set()
    order = ["1", "2", "3", "4"] if include_group else ["1", "2", "4"]
    for index, scenario in enumerate(order):
        if index:
            print()
        cancelled, used = run_scenario(drop_assets(groups, banned), scenario)
        if cancelled:
            return
        banned |= used
    if not include_group:
        print("\nScenario 3 skipped.")


def main():
    """Run all scenarios in order, or a single chosen scenario."""
    global BASE, KEY, APPLY
    if "IMMICH_URL" not in os.environ or "IMMICH_API_KEY" not in os.environ:
        sys.exit("Set IMMICH_URL and IMMICH_API_KEY.")
    BASE = os.environ["IMMICH_URL"].rstrip("/") + "/api"
    KEY = os.environ["IMMICH_API_KEY"]
    APPLY = "--apply" in sys.argv

    chosen = scenario_from_args(sys.argv)
    include_group = True
    if chosen is None:
        include_group = confirm("Enable automatic grouping of photos if they are similar? [yes/N] ")

    groups = call("GET", "/duplicates")
    if not isinstance(groups, list):
        sys.exit("Unexpected response from /duplicates.")
    if chosen is None:
        run_all(groups, include_group)
    else:
        run_scenario(groups, chosen)


if __name__ == "__main__":
    main()
