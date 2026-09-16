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
                parents=tuple(
                    parent for parent in parents.split() if parent
                ),
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
    rows: list[GraphRow] = []

    for commit in commits:
        if commit.sha not in active:
            active.append(commit.sha)

        lane = active.index(commit.sha)
        before_count = len(active)
        glyphs = ["| " for _ in active]
        glyphs[lane] = "● "
        suffix = (
            "┬"
            if len(commit.parents) > 1
            else ("╵" if not commit.parents else "")
        )
        rows.append(
            GraphRow(
                commit,
                lane,
                before_count,
                "".join(glyphs) + suffix,
            )
        )

        active.pop(lane)
        for parent in reversed(commit.parents):
            if parent not in active:
                active.insert(lane, parent)

        # Bound corrupt or adversarial history even though normal graphs are
        # far smaller than this limit.
        if len(active) > 256:
            raise ValueError("Commit graph exceeds the supported lane count")

    return tuple(rows)