"""PR #187 review: is_job_cancelled's "missing row = cancelled" reading must
stay scoped to the import path (is_import_cancelled). is_job_cancelled is
shared with 7 call sites in generation_jobs.py -- a generation job whose row
was deleted for an unrelated reason (e.g. a course or tenant hard-delete)
must not be silently read as "cancelled".
"""
from promptops_app.jobs.job_status import is_import_cancelled, is_job_cancelled


def test_missing_job_row_is_not_cancelled_by_default(db):
    """Default behaviour (generation_jobs.py's call shape) is unchanged."""
    assert is_job_cancelled(db, "nonexistent-job-id") is False


def test_missing_job_row_is_cancelled_for_the_import_path(db):
    """is_import_cancelled is the only caller allowed to read a purged
    (deleted) job row as a cancel -- see import_jobs.py."""
    assert is_import_cancelled(db, "nonexistent-job-id") is True
