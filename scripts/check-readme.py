"""check-readme: README.md stays in line with its sources (decision 46, change public-readme).

README is written by hand; this check fails when it drifts:
  1. order of the "## " sections;
  2. facts (slogan, "what is", install command, requirements, Works with) differ from docs/facts.yaml;
  3. "What you get" blocks differ from docs/advantages.yaml;
  4. a yorath.io/docs/... address has no file in docs/ (rule: "docs" in docs/facts.yaml);
  5. a Quick start recipe's code is not on the docs page it links to;
  6. a product version number appears;
  7. install.md is mentioned without a recorded agent pass (launch condition 10 of the file given
     with --launch-gate; without the file the rule is not checked).

Usage: python scripts/check-readme.py [repo root] [--launch-gate FILE]      (make check-readme)
Exit code 1 with one line per problem.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

SECTIONS = [
    "Install",
    "Quick start",
    "What you get",
    "Security",
    "Documentation",
    "Roadmap",
    "Community and contributing",
    "Build from source",
    "License and editions",
]
BLOCK_NAMES = {"run": "Run", "connect": "Connect", "control": "Control", "scale": "Scale"}


def norm(s: str) -> str:
    """Whitespace-insensitive comparison: line breaks and indentation don't matter."""
    return re.sub(r"\s+", " ", s).strip()


def h2_sections(readme: str) -> dict[str, str]:
    out: dict[str, str] = {}
    parts = re.split(r"^## +(.+?)\s*$", readme, flags=re.M)
    out["_top"] = parts[0]
    for i in range(1, len(parts), 2):
        out[parts[i]] = parts[i + 1]
    return out


def code_blocks(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^\s*```[^\n]*\n(.*?)^\s*```", text, flags=re.M | re.S)]


def doc_file(root: Path, url: str, rule: dict) -> Path | None:
    base = rule["base_url"]
    if not url.startswith(base):
        return None
    rel = url[len(base):].split("#")[0].strip("/")
    if not rel:
        return root / "docs" / "index.md"
    return root / "docs" / f"{rel}.md"


def condition_done(gate: Path | None, n: int) -> bool | None:
    """None when no launch-gate file is given or it is absent (the public tree has none)."""
    f = gate
    if f is None or not f.exists():
        return None
    c = (yaml.safe_load(f.read_text()) or {}).get("conditions", {}).get(n) or {}
    return c.get("status") == "done" and bool(c.get("date")) and bool(c.get("evidence"))


def check(root: Path, gate: Path | None = None) -> list[str]:
    errors: list[str] = []
    readme = (root / "README.md").read_text(encoding="utf-8")
    facts = yaml.safe_load((root / "docs" / "facts.yaml").read_text(encoding="utf-8"))
    adv = yaml.safe_load((root / "docs" / "advantages.yaml").read_text(encoding="utf-8"))
    rule = facts["docs"]
    secs = h2_sections(readme)
    nreadme = norm(readme)

    # 1. sections and their order
    found = [h for h in secs if h != "_top"]
    if found != SECTIONS:
        errors.append(f"sections: expected {SECTIONS}, found {found}")

    # 2. facts
    top = norm(secs.get("_top", ""))
    for key in ("full_name", "slogan", "what_is"):
        if norm(str(facts[key])) not in top:
            errors.append(f"facts: README top lacks {key} from docs/facts.yaml: {facts[key]!r}")
    install = secs.get("Install", "")
    if not any(norm(b) == norm(facts["install_command"]) for b in code_blocks(install)):
        errors.append(f"facts: Install has no code block with the install command {facts['install_command']!r}")
    if norm(facts["requirements"]["line"]) not in norm(install):
        errors.append(f"facts: Install lacks the requirements line {facts['requirements']['line']!r}")
    if norm(f"Works with: {facts['works_with']['readme_line']}") not in norm(secs.get("What you get", "")):
        errors.append(f"facts: What you get lacks 'Works with: {facts['works_with']['readme_line']}'")

    # 3. advantages: each block's title is the file's; every bullet under it is one of the file's items
    wyg = secs.get("What you get", "")
    blocks = re.split(r"^\*\*(Run|Connect|Control|Scale) — (.+?)\*\*\s*$", wyg, flags=re.M)
    seen = {}
    for i in range(1, len(blocks), 3):
        seen[blocks[i]] = (blocks[i + 1], blocks[i + 2])
    for b in adv["blocks"]:
        name = BLOCK_NAMES[b["key"]]
        if name not in seen:
            errors.append(f"advantages: What you get has no **{name} — …** block")
            continue
        title, body = seen[name]
        if norm(title) != norm(b["title"]):
            errors.append(f"advantages: {name} title {title!r} differs from docs/advantages.yaml {b['title']!r}")
        bullets = [norm(m) for m in re.findall(r"^- (.+)$", body, flags=re.M)]
        items = {norm(i) for i in b["items"]}
        if len(bullets) < 2:
            errors.append(f"advantages: {name} has fewer than 2 points")
        for x in bullets:
            if x not in items:
                errors.append(f"advantages: {name} point not in docs/advantages.yaml: {x!r}")
    if re.search(r"federation", wyg, flags=re.I):
        errors.append("advantages: federation is Enterprise-only and must not be in What you get")

    # 4. documentation links
    for url in sorted(set(re.findall(r"https://yorath\.io/docs/[^\s)\"'>]*", readme))):
        f = doc_file(root, url, rule)
        if f is None or not f.exists():
            errors.append(f"links: {url} has no file in docs/ (expected {f.relative_to(root) if f else '?'})")

    # 5. Quick start recipes: first code block must be on the linked docs page
    qs = secs.get("Quick start", "")
    recipes = re.split(r"^### +(.+?)\s*$", qs, flags=re.M)
    for i in range(1, len(recipes), 2):
        name, body = recipes[i], recipes[i + 1]
        blocks_ = code_blocks(body)
        links = re.findall(r"https://yorath\.io/docs/[^\s)\"'>]*", body)
        if not blocks_ or not links:
            errors.append(f"recipes: {name!r} needs a code block and a link to its docs page")
            continue
        page = doc_file(root, links[-1], rule)
        if page is None or not page.exists():
            continue  # reported by rule 4
        if norm(blocks_[0]) not in norm("\n".join(code_blocks(page.read_text(encoding="utf-8")))):
            errors.append(f"recipes: {name!r}: its code is not on {page.relative_to(root)} (the page changed or the README did)")

    # 6. no product version number
    m = re.search(r"(?<![\w.])v?\d+\.\d+\.\d+(?!\w|\.\d)", readme)
    if m:
        errors.append(f"version: README must not carry a version number, found {m.group(0)!r}")

    # 7. install.md only after an agent passed it
    if "install.md" in nreadme and condition_done(gate, 10) is False:
        errors.append("install.md: README links it, but launch condition 10 (agent pass) has no evidence")

    return errors


def main() -> int:
    args = sys.argv[1:]
    gate = None
    if "--launch-gate" in args:
        i = args.index("--launch-gate")
        gate = Path(args[i + 1])
        del args[i : i + 2]
    root = Path(args[0]) if args else Path(__file__).resolve().parent.parent
    errors = check(root, gate)
    for e in errors:
        print(f"check-readme: {e}", file=sys.stderr)
    if errors:
        return 1
    print("check-readme: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
