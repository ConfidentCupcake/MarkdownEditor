"""Render the actual Qt widget; pass --demo to exercise multiple ancestry lanes."""

import argparse
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from git_implementation.git_service import GitHistoryWorker
from git_implementation.history import GitCommit, GitSnapshot
from git_ui.commit_graph import CommitGraphPanel


def main():
    """Save a reproducible widget screenshot without launching the editor."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    panel = CommitGraphPanel()
    panel.apply_theme(Path("markdowneditor_assets/themes/theme.json"))
    panel.resize(1450, 460)
    if args.demo:
        records = (
            (
                "c81d35ab",
                ("38fc406e", "8d5b124a"),
                "HEAD -> main, origin/main",
                "Merge Git history redesign",
            ),
            ("38fc406e", ("4aba32be",), "", "Fix terminal paste shortcuts"),
            (
                "8d5b124a",
                ("449acf62", "8be4cd63"),
                "feature/git-history",
                "Merge theme support into history",
            ),
            ("449acf62", ("4aba32be",), "", "Paint connected ancestry lanes"),
            ("8be4cd63", ("4aba32be",), "feature/themes", "Add readable branch and tag labels"),
            ("4aba32be", ("224eef07",), "tag: v1.9.6", "Preserve history selection during refresh"),
            ("224eef07", ("110acebe",), "", "Handle detached HEAD history"),
            ("110acebe", (), "", "Initial commit"),
        )
        commits = tuple(
            GitCommit(sha, parents, "David Schubba", "2026-09-16T12:48:35+02:00", refs, message)
            for sha, parents, refs, message in records
        )
        panel.set_snapshot(GitSnapshot(Path.cwd(), "main", "c81d35ab", commits))
    else:
        worker = GitHistoryWorker(Path.cwd())
        worker.succeeded.connect(panel.set_snapshot)
        worker.failed.connect(lambda message: parser.error(message))
        worker.run()
    panel.show()
    app.processEvents()
    if not panel.grab().save(str(args.output)):
        raise RuntimeError("Could not save screenshot")
    panel.close()


if __name__ == "__main__":
    main()
