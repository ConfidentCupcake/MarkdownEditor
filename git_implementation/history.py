"""Pure Git history values, parser and compact topological lane layout."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitCommit:
    """One immutable commit returned by the selected repository."""

    sha: str
    parents: tuple[str, ...]
    author: str
    authored_at: str
    decorations: str
    subject: str


@dataclass(frozen=True)
class GitSnapshot:
    """Branch/HEAD identity and a topologically ordered history page."""

    root: Path
    branch: str | None
    head_short: str
    commits: tuple[GitCommit, ...]


@dataclass
class GraphRow:
    """A commit plus a compact text representaion of its active lanes."""

    commit: GitCommit
    lane: int
    lane_count: int
    graph_text: str
    color_index: int = 0
    incoming: tuple[tuple[int, int], ...] = ()
    outgoing: tuple[tuple[int, int, int], ...] = ()


def parse_log(output: str) -> tuple[GitCommit, ...]:
    """Parse the record/unit-separated output produced by GitHistoryWorker.

    Args:
        output: Text whose records start with ``\x1e`` and whose six fields
            are separated by ``\x1f``.

    Returns:
        Immutable commits in the order provided by ``git log``.

    Raises:
        ValueError: If a record is missing fields or its object ID.
    """
    commits: list[GitCommit] = []
    for raw_record in output.split("\x1e"):
        record = raw_record.strip("\r\n")
        if not record:
            continue

        fields = record.split("\x1f", 5)
        if len(fields) != 6:
            raise ValueError("Malformed git log record")

        sha, parents, author, authored_at, decorations, subject = fields
        if not sha:
            raise ValueError("Git log record has no object id")

        commits.append(
            GitCommit(
                sha=sha,
                # A merge commit can have two or more parents, so the model's
                # field is deliberately plural.
                parents=tuple(parent for parent in parents.split() if parent),
                author=author,
                authored_at=authored_at,
                decorations=decorations,
                subject=subject,
            )
        )

    return tuple(commits)


def layout_graph(commits: tuple[GitCommit, ...]) -> tuple[GraphRow, ...]:
    """Assign stable columns and mark merges for a compact graph view."""
    active: list[str] = []
    colors: dict[str, int] = {}
    next_color = 0
    rows: list[GraphRow] = []

    for commit in commits:
        # The previous row's bottom lanes are this row's incoming lanes.
        incoming = tuple((i, colors[sha]) for i, sha in enumerate(active))
        if commit.sha not in active:
            active.append(commit.sha)
            colors[commit.sha] = next_color
            next_color += 1

        lane = active.index(commit.sha)
        node_color = colors[commit.sha]
        before = list(active)
        before_count = len(active)
        glyphs = ["| " for _ in active]
        glyphs[lane] = "● "
        suffix = "┬" if len(commit.parents) > 1 else ("╵" if not commit.parents else "")
        active.pop(lane)
        for number, parent in enumerate(commit.parents):
            if parent not in active:
                active.insert(min(lane + number, len(active)), parent)
                # Continue the first-parent line; side parents get another color.
                colors[parent] = node_color if number == 0 else next_color
                if number:
                    next_color += 1

        outgoing = [
            (i, active.index(sha), colors[sha]) for i, sha in enumerate(before) if sha != commit.sha
        ]
        outgoing.extend(
            (lane, active.index(parent), colors[parent]) for parent in dict.fromkeys(commit.parents)
        )
        rows.append(
            GraphRow(
                commit,
                lane,
                max(before_count, len(active)),
                "".join(glyphs) + suffix,
                node_color,
                incoming,
                tuple(outgoing),
            )
        )

        # Bound corrupt or adversarial history even though normal graphs are
        # far smaller than this limit.
        if len(active) > 256:
            raise ValueError("Commit graph exceeds the supported lane count")

    return tuple(rows)
