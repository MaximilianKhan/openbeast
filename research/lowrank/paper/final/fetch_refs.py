#!/usr/bin/env python3
"""Regenerate refs.json — the bibliographic record behind the reference list.

    python3 fetch_refs.py            # parse refs/arxiv-*.xml  -> refs.json  (offline)
    python3 fetch_refs.py --fetch    # re-download the arXiv API responses first

Nothing in refs.json is typed by hand. Title, authors, year and journal
reference come from the saved arXiv API responses in refs/; the venue comes
from the arXiv record (journal_ref, else a conference named in its comment),
else from the venue the project bibliography (../references.md) states, and
refs.json keeps the sentence each venue was read from; `verified` is false when the id did
not resolve or when refs/review.json records that the arXiv record is not the
work the paper's text describes. refs/review.json is the one hand-kept input:
the outcome of reading every fetched title against the text's description,
plus the venues stated only by references.md that are too soft to print.
"""
import json, pathlib, re, sys, time, urllib.request
import xml.etree.ElementTree as ET

import build

HERE = pathlib.Path(__file__).parent
RAW = HERE / "refs"
API = "http://export.arxiv.org/api/query?max_results={n}&id_list={ids}"
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
BATCH = 25
VENUE_NAMES = ["NeurIPS", "ICML", "ICLR", "AAAI", "MLSys", "COLM", "ACL", "EMNLP", "NAACL"]
VENUES = "(" + "|".join(VENUE_NAMES) + ")"


def cited():
    """The cited ids in order of first appearance, exactly as build.py numbers them."""
    build.refs.clear()
    parts = [p.read_text().strip() for p in sorted((HERE / "sections").glob("*.md"))]
    build.cite(build.sources("\n\n".join(parts)))
    return list(build.refs)


def fetch(ids):
    RAW.mkdir(exist_ok=True)
    for old in RAW.glob("arxiv-*.xml"):
        old.unlink()
    for b in range(0, len(ids), BATCH):
        chunk = ids[b:b + BATCH]
        url = API.format(n=len(chunk), ids=",".join(chunk))
        req = urllib.request.Request(url, headers={"User-Agent": "openbeast-paper-refs/1.0"})
        data = urllib.request.urlopen(req, timeout=60).read()
        (RAW / f"arxiv-{b // BATCH + 1:02d}.xml").write_bytes(data)
        print(f"fetched {len(chunk)} ids -> arxiv-{b // BATCH + 1:02d}.xml ({len(data)} bytes)")
        time.sleep(3)


def squash(s):
    return re.sub(r"\s+", " ", s or "").strip()


def parse():
    out = {}
    for f in sorted(RAW.glob("arxiv-*.xml")):
        for e in ET.parse(f).getroot().findall("a:entry", NS):
            m = re.search(r"abs/(\d{4}\.\d{4,5})(v\d+)?$", e.findtext("a:id", "", NS))
            title = squash(e.findtext("a:title", "", NS))
            if not m or not title or title == "Error":
                continue
            out[m.group(1)] = {
                "title": title,
                "authors": [squash(a.findtext("a:name", "", NS)) for a in e.findall("a:author", NS)],
                "year": int(e.findtext("a:published", "", NS)[:4]),
                "published": e.findtext("a:published", "", NS)[:10],
                "journal_ref": squash(e.findtext("x:journal_ref", "", NS)) or None,
                "comment": squash(e.findtext("x:comment", "", NS)) or None,
                "primary_category": (e.find("x:primary_category", NS).get("term")
                                     if e.find("x:primary_category", NS) is not None else None),
            }
    return out


def venue_in(text):
    """(name, year) of a conference named with its year in free text, else None.

    Reads 'Accepted at ICML2024', "ICLR'26 camera ready", 'ICLR 2026 workshops',
    '(COLM), 2024'. Only the spelling is normalized; no venue is inferred.
    """
    m = re.search(VENUES + r"\W{0,3}(\d{4}|\d{2})(?!\d)(\s+workshop)?", text or "", re.I)
    if not m:
        return None
    year = int(m.group(2)) if len(m.group(2)) == 4 else 2000 + int(m.group(2))
    name = next(v for v in VENUE_NAMES if v.lower() == m.group(1).lower())
    return name + (" Workshop" if m.group(3) else ""), year


def venue(rec, name):
    """Venue as stated by the arXiv record, else by the project bibliography.

    Order: the record's journal_ref verbatim (unless it is a bare URL), a
    conference named in the record's comment, a conference named in
    ../references.md. A references.md entry that marks itself soft is skipped.
    Returns (venue, venue_year, source, evidence); venue_year None means the
    venue string already carries its own date.
    """
    jr = rec["journal_ref"]
    if jr and not re.match(r"https?://\S+$", jr):
        return jr, None, "arxiv journal_ref", jr
    hit = venue_in(rec["comment"])
    if hit:
        return hit[0], hit[1], "arxiv comment", rec["comment"]
    paren = re.search(r"\(([^()]*)\)\s*$", name or "")
    if paren and not re.search(r"\bsoft\b|\bclaim|\?|unverified|submitted", paren.group(1), re.I):
        hit = venue_in(paren.group(1))
        if hit:
            return hit[0], hit[1], "references.md", name
    return None, None, None, None


def main():
    ids = cited()
    if "--fetch" in sys.argv:
        fetch(ids)
    got = parse()
    names = build.load_refs()
    names.update({k: v for k, v in build.EXTRA_NAMES.items() if k not in names})
    review_file = RAW / "review.json"
    review = json.loads(review_file.read_text()) if review_file.exists() else {}
    mismatch = review.get("mismatch", {})
    entries = []
    for n, i in enumerate(ids, 1):
        e = {"n": n, "id": i, "short_name": names.get(i)}
        rec = got.get(i)
        if rec is None:
            e.update(verified=False, problem="arXiv API returned no record for this id")
        elif i in mismatch:
            e.update(verified=False, problem=mismatch[i], arxiv_record=rec)
        else:
            v, vy, vsrc, vev = venue(rec, names.get(i))
            if vsrc == "references.md" and i in review.get("venue_withheld", {}):
                e["venue_withheld"] = review["venue_withheld"][i]
                v = vy = vsrc = vev = None
            e.update(verified=True, title=rec["title"], authors=rec["authors"], year=rec["year"],
                     venue=v, venue_year=vy, venue_source=vsrc, venue_evidence=vev)
        entries.append(e)
    (HERE / "refs.json").write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n")
    bad = [e for e in entries if not e["verified"]]
    print(f"cited: {len(ids)}  resolved: {sum(1 for i in ids if i in got)}  "
          f"verified: {len(entries) - len(bad)}  unverified: {len(bad)}")
    for e in bad:
        print(f"  [{e['n']}] {e['id']}  ({e['short_name']})  {e['problem']}")


if __name__ == "__main__":
    main()
