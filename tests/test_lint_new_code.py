"""
scripts/lint_new_code.py only reports findings on lines a diff touched, so
the one piece of real logic in it is turning a unified diff into a set of
"these lines changed" per file. This exercises that parsing directly against
crafted diff text — no git process, no repo state — because a bug there
either hides real findings (a line number computed wrong) or reports on code
nobody touched (a file that should have been skipped).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.lint_new_code import parse_unified_diff  # noqa: E402


def test_single_hunk_explicit_count():
    diff = (
        "diff --git a/app/foo.py b/app/foo.py\n"
        "index 111..222 100644\n"
        "--- a/app/foo.py\n"
        "+++ b/app/foo.py\n"
        "@@ -10,0 +11,2 @@ def foo():\n"
        "+    a = 1\n"
        "+    b = 2\n"
    )
    assert parse_unified_diff(diff) == {"app/foo.py": {11, 12}}


def test_hunk_header_omits_count_of_one():
    diff = (
        "diff --git a/app/foo.py b/app/foo.py\n"
        "--- a/app/foo.py\n"
        "+++ b/app/foo.py\n"
        "@@ -5 +5 @@\n"
        "-old\n"
        "+new\n"
    )
    assert parse_unified_diff(diff) == {"app/foo.py": {5}}


def test_multiple_hunks_in_one_file():
    diff = (
        "diff --git a/app/foo.py b/app/foo.py\n"
        "--- a/app/foo.py\n"
        "+++ b/app/foo.py\n"
        "@@ -1,0 +2,1 @@\n"
        "+first\n"
        "@@ -20,0 +22,2 @@\n"
        "+second\n"
        "+third\n"
    )
    assert parse_unified_diff(diff) == {"app/foo.py": {2, 22, 23}}


def test_pure_deletion_hunk_adds_no_lines():
    diff = (
        "diff --git a/app/foo.py b/app/foo.py\n"
        "--- a/app/foo.py\n"
        "+++ b/app/foo.py\n"
        "@@ -8,2 +7,0 @@\n"
        "-removed one\n"
        "-removed two\n"
    )
    # The file has an entry (it was touched) but no changed lines to lint.
    assert parse_unified_diff(diff) == {"app/foo.py": set()}


def test_deleted_file_is_dropped_entirely():
    diff = (
        "diff --git a/app/gone.py b/app/gone.py\n"
        "deleted file mode 100644\n"
        "--- a/app/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,3 +0,0 @@\n"
        "-a\n"
        "-b\n"
        "-c\n"
    )
    assert parse_unified_diff(diff) == {}


def test_new_file_counts_every_added_line():
    diff = (
        "diff --git a/app/new.py b/app/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/app/new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+one\n"
        "+two\n"
    )
    assert parse_unified_diff(diff) == {"app/new.py": {1, 2}}


def test_pure_rename_with_no_content_change_has_no_entry():
    diff = (
        "diff --git a/app/old_name.py b/app/new_name.py\n"
        "similarity index 100%\n"
        "rename from app/old_name.py\n"
        "rename to app/new_name.py\n"
    )
    assert parse_unified_diff(diff) == {}


def test_rename_with_edits_is_keyed_on_the_new_path():
    diff = (
        "diff --git a/app/old_name.py b/app/new_name.py\n"
        "similarity index 90%\n"
        "rename from app/old_name.py\n"
        "rename to app/new_name.py\n"
        "--- a/app/old_name.py\n"
        "+++ b/app/new_name.py\n"
        "@@ -3,0 +4,1 @@\n"
        "+added after the rename\n"
    )
    assert parse_unified_diff(diff) == {"app/new_name.py": {4}}


def test_multiple_files_are_independent():
    diff = (
        "diff --git a/app/a.py b/app/a.py\n"
        "--- a/app/a.py\n"
        "+++ b/app/a.py\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
        "diff --git a/app/b.py b/app/b.py\n"
        "--- a/app/b.py\n"
        "+++ b/app/b.py\n"
        "@@ -9,0 +10,1 @@\n"
        "+z\n"
    )
    assert parse_unified_diff(diff) == {"app/a.py": {1}, "app/b.py": {10}}
