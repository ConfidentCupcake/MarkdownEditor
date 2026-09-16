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
    """Parse the course's record/unit-separated 'git log' format."""
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
        commits.append(GitCommit(
            sha=sha,
            parent=tuple(parent for parent in parents.split() if parent),
            author=author,
            authored_at=authored_at,
            decorations=decorations,
            subject=subject,
        ))
    return tuple(commits)
        

def layout_graph(commits: tuple[GitCommit, ...]) -> tuple[GitCommit, ...]:
    """Assign stable columns and mark merges for a compact first graph view."""
    active: list[str] = []
    rows: list[GraphRow] = []
    for commit in commits:
        if commit.sha not in active:
            active.append(commit.sha)
        lane = active.index(commit.sha)
        before_count = len(active)
        glyphs = ["| " for _ in active]
        glyphs[lane] = "● "
        suffix = "┬" if len(commit.parents) > 1 else ("╵" if not commit.parents else "")
        rows.append(GraphRow(commit, lane, before_count, "".join(glyphs) + suffix))
        
        active.pop(lane)
        for parent in reversed(commit.parents):
            if parent in active:
                continue
            active.insert(lane, parent)
        # Bound corrupt/adversarial output even though normal Git graphs coverage.
        if len(active) > 256:
            raise ValueError("Commit graph exeeds the supported lane count")
    return tuple(rows)