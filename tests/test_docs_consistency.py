"""Executable documentation-integrity suite for Argus.

The repo is design-stage: the only honest testable surface today is the
documentation itself. These tests encode the hardening invariants so the
docs keep guarding themselves as code lands later:

  [a] required files exist
  [b] relative markdown links in README.md / MAP.md resolve
  [c] every ``DESIGN.md:N`` anchor in MAP.md still points at the cited thing
  [d] state vocabulary is consistent between README.md and DESIGN.md
  [e] .gitignore covers runtime-state / secret patterns
  [f] LICENSE is the MIT license attributed to Antreas Antoniou
  [g] the README ASCII board mockup is internally consistent (property-style)

All paths resolve relative to the repo root (the parent of ``tests/``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = ("DESIGN.md", "README.md", "LICENSE", ".gitignore", "MAP.md")

#: States every vocabulary must contain (``idle`` is mandatory in both docs).
CORE_STATES = frozenset({"blocked", "idle", "done", "dead"})

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
DESIGN_REF_RE = re.compile(r"DESIGN\.md:(\d+)")
CODE_SPAN_RE = re.compile(r"`([^`]+)`")
BOLD_SPAN_RE = re.compile(r"\*\*([^*]+)\*\*")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z_-]{3,}")
STATE_LIST_RE = re.compile(r"^[a-z]+(?: / [a-z]+)+$")
EYES_OPEN_RE = re.compile(r"(\d+)\s+eyes?\s+open")

SESSION_MARKERS = "▸●✓☠"
BOX_TOP_CHARS = "┌╭"
BOX_BORDER_CHARS = "┌└├╭╰"

# Words too generic to identify an anchor target (plus the citation itself).
_STOPWORDS = frozenset({"design", "designmd", "md", "with", "that", "this", "from"})


def doc(name: str, root: Path = REPO_ROOT) -> str:
    return (root / name).read_text(encoding="utf-8")


def strip_fences(markdown: str) -> str:
    """Drop fenced code blocks so ``` fences cannot break span pairing."""
    kept: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# [a] required files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", REQUIRED_FILES)
def test_required_file_exists(name: str) -> None:
    path = REPO_ROOT / name
    assert path.is_file(), f"required file missing: {name}"
    assert path.stat().st_size > 0, f"required file is empty: {name}"


# ---------------------------------------------------------------------------
# [b] relative markdown links resolve
# ---------------------------------------------------------------------------


def relative_link_targets(markdown: str) -> list[str]:
    """All relative-file link targets in a markdown text (anchors stripped)."""
    targets = []
    for target in MD_LINK_RE.findall(strip_fences(markdown)):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]
        if target:
            targets.append(target)
    return targets


@pytest.mark.parametrize("name", ["README.md", "MAP.md"])
def test_relative_markdown_links_resolve(name: str) -> None:
    broken = [
        target
        for target in relative_link_targets(doc(name))
        if not (REPO_ROOT / target).exists()
    ]
    assert not broken, f"{name} has broken relative links: {broken}"


# ---------------------------------------------------------------------------
# [c] DESIGN.md:N anchor integrity
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Case-fold and drop markdown decoration so containment is not brittle."""
    return text.lower().replace("`", "").replace("*", "")


def _span_candidates(line: str) -> list[str]:
    """Code/bold spans cited on a line (the identifiers an anchor names)."""
    spans = []
    for match in CODE_SPAN_RE.finditer(line):
        spans.append(match.group(1))
    for match in BOLD_SPAN_RE.finditer(line):
        spans.append(match.group(1).strip("`"))
    return [s for s in spans if "DESIGN.md" not in s and _normalize(s).strip()]


def _word_candidates(text: str) -> list[str]:
    words = [w.lower() for w in WORD_RE.findall(text)]
    return [w for w in words if w not in _STOPWORDS]


def _candidates(map_lines: list[str], index: int) -> list[str]:
    """Identifier candidates for the DESIGN.md:N reference on map_lines[index].

    Prefer the explicit code/bold spans on the citing line. A bare heading
    (e.g. ``### Per-session state machine (DESIGN.md:80)``) cites its whole
    section, so fall back to the heading's words plus its section body words.
    """
    line = map_lines[index]
    spans = _span_candidates(DESIGN_REF_RE.sub("", line))
    if spans:
        return spans
    candidates = _word_candidates(DESIGN_REF_RE.sub("", line))
    if line.lstrip().startswith("#"):
        for body_line in map_lines[index + 1 :]:
            if body_line.lstrip().startswith("#"):
                break
            candidates.extend(_word_candidates(body_line))
    return candidates


def find_anchor_violations(map_text: str, design_text: str) -> list[str]:
    """Every ``DESIGN.md:N`` in map_text must cite what line N actually holds.

    Line N is 1-indexed; the cited identifier must appear (case-insensitive,
    markdown-decoration-insensitive) within a +/-1-line window around N.
    """
    design_lines = design_text.splitlines()
    map_lines = map_text.splitlines()
    violations = []
    for index, line in enumerate(map_lines):
        for match in DESIGN_REF_RE.finditer(line):
            n = int(match.group(1))
            where = f"MAP line {index + 1} cites DESIGN.md:{n}"
            if not 1 <= n <= len(design_lines):
                violations.append(
                    f"{where}, but DESIGN.md has only {len(design_lines)} lines"
                )
                continue
            window = _normalize(" ".join(design_lines[max(0, n - 2) : n + 1]))
            candidates = _candidates(map_lines, index)
            if not candidates:
                violations.append(f"{where} with no extractable identifier")
            elif not any(_normalize(c) in window for c in candidates):
                violations.append(
                    f"{where}, but none of {candidates!r} appear near that line"
                )
    return violations


def test_map_design_anchors_are_current() -> None:
    violations = find_anchor_violations(doc("MAP.md"), doc("DESIGN.md"))
    assert not violations, "stale DESIGN.md anchors in MAP.md:\n" + "\n".join(violations)


def test_map_contains_design_anchors_at_all() -> None:
    """Guard the guard: MAP.md is expected to carry DESIGN.md:N anchors."""
    assert DESIGN_REF_RE.search(doc("MAP.md")), "MAP.md cites no DESIGN.md:N anchors"


# Property-style: a deliberately broken anchor in a scratch copy MUST fail.

_SCRATCH_DESIGN = "\n".join(
    [
        "# Design",  # 1
        "",  # 2
        "intro prose about nothing in particular",  # 3
        "",  # 4
        "- **`argusd`** — the daemon",  # 5
        "- **state store** — SQLite journal",  # 6
    ]
)


def test_anchor_checker_accepts_true_anchor() -> None:
    assert find_anchor_violations("| **argusd** | daemon (DESIGN.md:5) |", _SCRATCH_DESIGN) == []


def test_anchor_checker_tolerates_off_by_one() -> None:
    # +/-1-line window: a one-line drift is noise, not rot.
    assert find_anchor_violations("| **argusd** | daemon (DESIGN.md:4) |", _SCRATCH_DESIGN) == []


@pytest.mark.parametrize("broken_ref", ["DESIGN.md:3", "DESIGN.md:1", "DESIGN.md:99"])
def test_anchor_checker_flags_broken_anchor(broken_ref: str) -> None:
    map_text = f"| **argusd** | daemon ({broken_ref}) |"
    assert find_anchor_violations(map_text, _SCRATCH_DESIGN), (
        f"checker failed to flag broken anchor {broken_ref}"
    )


def test_anchor_checker_flags_break_in_scratch_copy_of_real_docs() -> None:
    """Acceptance property: rot injected into the real MAP.md is caught."""
    map_text = doc("MAP.md")
    match = DESIGN_REF_RE.search(map_text)
    assert match is not None
    # Redirect the first real anchor to DESIGN.md's blank line 2.
    broken = map_text[: match.start()] + "DESIGN.md:2" + map_text[match.end() :]
    assert find_anchor_violations(broken, doc("DESIGN.md")), (
        "checker failed to flag a deliberately broken anchor in a scratch copy"
    )


# ---------------------------------------------------------------------------
# [d] state-vocabulary consistency
# ---------------------------------------------------------------------------


def state_vocabulary(markdown: str) -> set[str]:
    """States named in slash-separated code spans like `a / b / c`."""
    states: set[str] = set()
    for span in CODE_SPAN_RE.findall(strip_fences(markdown)):
        if STATE_LIST_RE.match(span):
            states.update(part.strip() for part in span.split("/"))
    return states


def test_readme_states_are_subset_of_design_states() -> None:
    readme_states = state_vocabulary(doc("README.md"))
    design_states = state_vocabulary(doc("DESIGN.md"))
    assert readme_states, "README.md names no state vocabulary"
    assert readme_states <= design_states, (
        f"README.md names states DESIGN.md does not: {readme_states - design_states}"
    )


@pytest.mark.parametrize("name", ["README.md", "DESIGN.md"])
def test_core_states_present(name: str) -> None:
    states = state_vocabulary(doc(name))
    missing = CORE_STATES - states
    assert not missing, f"{name} state vocabulary is missing {sorted(missing)}"
    assert "idle" in states, f"idle is mandatory in {name}"


def test_design_includes_starting_state() -> None:
    assert "starting" in state_vocabulary(doc("DESIGN.md"))


# ---------------------------------------------------------------------------
# [e] .gitignore hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern", [".env", "config.local.toml", "*.db", "*.sqlite3", "*.sqlite"]
)
def test_gitignore_covers_runtime_state(pattern: str) -> None:
    entries = {
        line.strip()
        for line in doc(".gitignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert pattern in entries, f".gitignore does not cover {pattern!r}"


# ---------------------------------------------------------------------------
# [f] LICENSE
# ---------------------------------------------------------------------------


def test_license_is_mit_and_attributed() -> None:
    license_text = doc("LICENSE")
    assert "MIT License" in license_text
    assert "Antreas Antoniou" in license_text


# ---------------------------------------------------------------------------
# [g] property-style ASCII mockup checks
# ---------------------------------------------------------------------------


def fenced_blocks(markdown: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            if in_fence:
                blocks.append(current)
                current = []
            in_fence = not in_fence
        elif in_fence:
            current.append(line)
    return blocks


def ascii_boxes(markdown: str) -> list[list[str]]:
    """Fenced blocks that draw a box (contain a box-drawing top corner)."""
    return [
        block
        for block in fenced_blocks(markdown)
        if any(line.lstrip()[:1] in tuple(BOX_TOP_CHARS) for line in block if line.strip())
    ]


def find_box_violations(box: list[str]) -> list[str]:
    """Property checks over one ASCII board mockup box.

    * every box-drawing border row has the same width;
    * every interior row bounded by ``│`` matches the border width;
    * if a header advertises ``N eyes open``, exactly N session markers
      (▸ ● ✓ ☠) appear in the box — a row may carry more than one session.
    """
    violations = []
    rows = [line.rstrip() for line in box if line.strip()]
    border_widths = {
        len(row) for row in rows if row.lstrip()[:1] in tuple(BOX_BORDER_CHARS)
    }
    if len(border_widths) != 1:
        violations.append(f"inconsistent border widths: {sorted(border_widths)}")
    else:
        (width,) = border_widths
        for row in rows:
            if row.lstrip().startswith("│") and len(row) != width:
                violations.append(f"row width {len(row)} != border width {width}: {row!r}")
    header_eyes = EYES_OPEN_RE.search("\n".join(rows))
    if header_eyes:
        advertised = int(header_eyes.group(1))
        markers = sum(row.count(marker) for row in rows for marker in SESSION_MARKERS)
        if advertised != markers:
            violations.append(
                f"header says {advertised} eyes open but {markers} session rows drawn"
            )
    return violations


def test_readme_has_board_mockup() -> None:
    assert ascii_boxes(doc("README.md")), "README.md has no fenced ASCII board mockup"


def test_readme_mockup_boxes_are_consistent() -> None:
    for box in ascii_boxes(doc("README.md")):
        violations = find_box_violations(box)
        assert not violations, "README.md mockup is inconsistent:\n" + "\n".join(violations)


def test_readme_mockup_header_counts_eyes() -> None:
    boards = [box for box in ascii_boxes(doc("README.md")) if EYES_OPEN_RE.search("\n".join(box))]
    assert boards, "README.md board mockup has no 'N eyes open' header"


# Property-style mutations: the checker must reject broken mockups.

_GOOD_BOX = [
    "┌ ARGUS ──── 2 eyes open ┐",
    "│ ▸ one   blocked        │",
    "│ ● two   editing        │",
    "└─────────────────────────┘",
]


def _pad_box(box: list[str]) -> list[str]:
    width = max(len(row) for row in box)
    return [row[:-1].ljust(width - 1) + row[-1] for row in box]


def test_box_checker_accepts_consistent_box() -> None:
    assert find_box_violations(_pad_box(_GOOD_BOX)) == []


def test_box_checker_flags_ragged_border() -> None:
    ragged = _pad_box(_GOOD_BOX)
    ragged[-1] += "──"
    assert find_box_violations(ragged), "checker missed a ragged border row"


def test_box_checker_flags_eye_miscount() -> None:
    miscounted = [row.replace("2 eyes open", "3 eyes open") for row in _pad_box(_GOOD_BOX)]
    assert find_box_violations(miscounted), "checker missed an eyes-open miscount"
