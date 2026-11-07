#!/usr/bin/env bash
# create_issues.sh - peuple le dépôt GitHub en labels, milestones et issues
# depuis docs/backlog.md (spec §19.2).
#
# Usage :
#   scripts/create_issues.sh            # dry-run (défaut) : affiche le plan, ne crée rien
#   scripts/create_issues.sh --dry-run  # idem
#   scripts/create_issues.sh --apply    # crée ce qui manque
#
# Idempotent : les issues sont dédoublonnées sur leur titre exact, les labels
# et milestones sur leur nom. Ordre de création : labels -> milestones -> issues.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

MODE="dry-run"
for arg in "$@"; do
  case "$arg" in
    --dry-run) MODE="dry-run" ;;
    --apply)   MODE="apply" ;;
    -h|--help)
      sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "argument inconnu: $arg (voir --help)" >&2; exit 2 ;;
  esac
done

MODE="$MODE" python3 - <<'PYEOF'
import json, os, re, subprocess, sys, tempfile

MODE = os.environ["MODE"]
BACKLOG = "docs/backlog.md"

LABEL_STYLE = {
    "area:api": ("0075ca", "Zone : API"), "area:ui": ("0075ca", "Zone : UI"),
    "area:llm": ("0075ca", "Zone : LLM"), "area:engine": ("0075ca", "Zone : moteurs"),
    "area:static": ("0075ca", "Zone : statique"), "area:docs": ("0075ca", "Zone : docs"),
    "area:study": ("0075ca", "Zone : études"),
    "kind:feat": ("0e8a16", "Type : feature"), "kind:test": ("1d76db", "Type : test"),
    "kind:chore": ("ededed", "Type : chore"), "kind:spike": ("fbca04", "Type : spike"),
    "kind:docs": ("5319e7", "Type : documentation"),
}
MILESTONE_RE = re.compile(r"^## Vague \d+ — Milestone `([^`]+)`")
ISSUE_RE = re.compile(r"^### (P\d+) · (.+)$")
LABELS_RE = re.compile(r"^\*\*Labels\*\* (.+?) · \*\*Dép\.\*\* (.+)$")


def expand_deps(raw):
    raw = raw.strip()
    if raw in ("—", "-", ""):
        return []
    out = []
    for part in raw.split(","):
        part = part.strip()
        m = re.match(r"^(P\d+)\s*[–-]\s*(P\d+)$", part)
        if m:
            out += [f"P{i:02d}" for i in range(int(m.group(1)[1:]), int(m.group(2)[1:]) + 1)]
        elif re.match(r"^P\d+$", part):
            out.append(part)
        else:
            raise ValueError(f"dépendance non reconnue: {part!r}")
    return out


def parse_backlog():
    issues, milestone, cur = [], None, None
    for ln in open(BACKLOG, encoding="utf-8"):
        ln = ln.rstrip("\n")
        m = MILESTONE_RE.match(ln)
        if m:
            milestone = m.group(1)
            continue
        m = ISSUE_RE.match(ln)
        if m:
            cur = {"id": m.group(1), "title": m.group(2).strip(), "milestone": milestone,
                   "labels": [], "deps": [], "body": []}
            issues.append(cur)
            continue
        if cur is None:
            continue
        m = LABELS_RE.match(ln)
        if m:
            cur["labels"] = re.findall(r"`([^`]+)`", m.group(1))
            cur["deps"] = expand_deps(m.group(2))
            continue
        if ln.startswith(("## ", "### ")):
            cur = None
            continue
        cur["body"].append(ln)
    for it in issues:
        b = it["body"]
        while b and not b[0].strip():
            b.pop(0)
        while b and not b[-1].strip():
            b.pop()
        it["body"] = "\n".join(b)
    if len(issues) != 50:
        raise SystemExit(f"attendu 50 issues dans {BACKLOG}, trouvé {len(issues)}")
    labels = sorted({l for it in issues for l in it["labels"]})
    milestones = []
    for it in issues:
        if it["milestone"] not in milestones:
            milestones.append(it["milestone"])
    return labels, milestones, issues


def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} a échoué: {r.stderr.strip()}")
    return r.stdout


repo = gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
labels_ex = set(gh("label", "list", "--repo", repo, "--json", "name", "--jq", ".[].name").split())
miles_ex = set(gh("api", f"repos/{repo}/milestones?state=all", "--jq", ".[].title").splitlines())
titles_ex = set(gh("issue", "list", "--repo", repo, "--state", "all", "--limit", "500",
                   "--json", "title", "--jq", ".[].title").splitlines())

labels, milestones, issues = parse_backlog()
todo_labels = [l for l in labels if l not in labels_ex]
todo_miles = [m for m in milestones if m not in miles_ex]
todo_issues = [i for i in issues if f"{i['id']} · {i['title']}" not in titles_ex]

print(f"[{MODE}] dépôt {repo} : {len(todo_labels)} labels, {len(todo_miles)} milestones, "
      f"{len(todo_issues)} issues à créer (sur {len(labels)}/{len(milestones)}/{len(issues)})")
for l in todo_labels:
    print(f"  label     {l}")
for m in todo_miles:
    print(f"  milestone {m}")
for i in todo_issues:
    print(f"  issue     {i['id']} · {i['title']} [{i['milestone']}]")
if MODE == "dry-run":
    sys.exit(0)

for l in todo_labels:
    color, desc = LABEL_STYLE[l]
    gh("label", "create", l, "--repo", repo, "--color", color, "--description", desc)
    print(f"label créé : {l}")

mile_num = {}
for m in todo_miles:
    out = gh("api", "-X", "POST", f"repos/{repo}/milestones", "-f", f"title={m}")
    mile_num[m] = json.loads(out)["number"]
    print(f"milestone créé : {m} (#{mile_num[m]})")
for line in gh("api", f"repos/{repo}/milestones?state=all",
               "--jq", '.[] | .title + " " + (.number | tostring)').splitlines():
    t, n = line.rsplit(" ", 1)
    mile_num.setdefault(t, int(n))

id2num, tmp = {}, tempfile.mkdtemp(prefix="petrichor-issues-")
for it in issues:
    title = f"{it['id']} · {it['title']}"
    if title in titles_ex:
        continue
    deps = ", ".join(f"#{id2num[d]}" for d in it["deps"] if d in id2num)
    body = it["body"] + "\n\n---\n**Dépendances.** " + (f"blocked-by: {deps}" if deps else "aucune.")
    bf = os.path.join(tmp, f"{it['id']}.md")
    open(bf, "w", encoding="utf-8").write(body)
    args = ["issue", "create", "--repo", repo, "--title", title, "--body-file", bf,
            "--milestone", it["milestone"]]
    for l in it["labels"]:
        args += ["--label", l]
    out = gh(*args)
    num = int(re.search(r"/issues/(\d+)", out).group(1))
    id2num[it["id"]] = num
    print(f"issue créée : #{num} {title} [{it['milestone']}]")

print(f"terminé : {len(id2num)} issues créées au total dans ce run")
PYEOF
