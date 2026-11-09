#!/usr/bin/env python3
import os, re, sys, json, urllib.request
from datetime import datetime, timedelta
from collections import defaultdict

BASE = os.environ["IMMICH_URL"].rstrip("/") + "/api"
KEY = os.environ["IMMICH_API_KEY"]
APPLY = "--apply" in sys.argv
SCENARIO = "1"
if "--scenario" in sys.argv:
    SCENARIO = sys.argv[sys.argv.index("--scenario") + 1]
if SCENARIO not in ("1", "2"):
    sys.exit("--scenario должен быть 1 или 2")

SUFFIX = re.compile(r"(?:\s*\(\d+\))+(?=\.[^.]+$)")
_ISO = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?")


def norm(name):
    return SUFFIX.sub("", name).lower()


def size(a):
    return (a.get("exifInfo") or {}).get("fileSizeInByte")


def parse(s):
    m = _ISO.match(s)
    y, mo, d, h, mi, sec = map(int, m.groups()[:6])
    frac = float("0." + m.group(7)) if m.group(7) else 0.0
    return datetime(y, mo, d, h, mi, sec) + timedelta(seconds=frac)


def date_ok(a, b):
    dt = abs((parse(a["localDateTime"]) - parse(b["localDateTime"])).total_seconds())
    if dt <= 2:
        return True
    if dt > 14 * 3600 + 2:
        return False
    r = dt % 1800
    return r <= 2 or r >= 1798


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "x-api-key": KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def by_size(items):
    # самый тяжёлый первым; при равенстве — более ранний fileCreatedAt
    return sorted(items, key=lambda a: (-size(a), a.get("fileCreatedAt", "")))


groups = call("GET", "/duplicates")
to_delete, review, skipped = [], [], []

if SCENARIO == "1":
    # одинаковое имя (с точностью до "(N)"), один владелец, дата с точностью до часового пояса
    buckets = defaultdict(dict)
    for g in groups:
        for a in g["assets"]:
            buckets[(a["ownerId"], norm(a["originalFileName"]))][a["id"]] = a
    for assets in buckets.values():
        items = list(assets.values())
        if len(items) < 2:
            continue
        if any(size(a) is None for a in items):
            skipped.append([a["originalFileName"] for a in items])
            continue
        items = by_size(items)
        for a in items[1:]:
            (to_delete if date_ok(items[0], a) else review).append((items[0], a))
else:
    # одна секунда съёмки, один владелец, в пределах одной группы дублей Immich
    for g in groups:
        b = defaultdict(list)
        for a in g["assets"]:
            b[(a["ownerId"], parse(a["localDateTime"]).replace(microsecond=0))].append(a)
        for items in b.values():
            if len(items) < 2:
                continue
            if any(size(a) is None for a in items):
                skipped.append([a["originalFileName"] for a in items])
                continue
            items = by_size(items)
            if len(items) > 2 or len({a.get("type") for a in items}) > 1:
                for a in items[1:]:
                    review.append((items[0], a))
            else:
                to_delete.append((items[0], items[1]))


def show(k, a, mark):
    diff = "" if norm(k["originalFileName"]) == norm(a["originalFileName"]) else "  [имена разные]"
    print("KEEP {} ({} B) {} id={}".format(k["originalFileName"], size(k), k["localDateTime"], k["id"]))
    print("  {} {} ({} B) {} id={}{}".format(mark, a["originalFileName"], size(a), a["localDateTime"], a["id"], diff))


print("=== СЦЕНАРИЙ {} ===".format(SCENARIO))
print("=== DELETE ===")
for k, a in to_delete:
    show(k, a, "DEL")
print("\n=== REVIEW (не удаляются) ===")
for k, a in review:
    show(k, a, "???")
print("\n=== SKIPPED (нет размера) ===")
for s in skipped[:50]:
    print(" ", s)
print("\nК удалению: {}, на ревью: {}, пропущено: {}".format(len(to_delete), len(review), len(skipped)))

if APPLY and to_delete:
    ids = list({a["id"] for _, a in to_delete})
    if input("Отправить {} файлов в корзину? [yes/N] ".format(len(ids))).strip() != "yes":
        print("Отменено.")
        sys.exit(0)
    for i in range(0, len(ids), 100):
        call("DELETE", "/assets", {"ids": ids[i:i + 100], "force": False})
        print("  отправлено {}/{}".format(min(i + 100, len(ids)), len(ids)))
    print("Готово: в корзину отправлено", len(ids))
elif APPLY:
    print("Нечего удалять.")
else:
    print("Dry-run: ничего не удалено. Для удаления добавь --apply.")