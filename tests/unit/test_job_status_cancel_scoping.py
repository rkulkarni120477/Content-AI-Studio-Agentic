"""PR #187 review: is_job_cancelled's "missing row = cancelled" reading must
stay scoped to the import path (is_import_cancelled). is_job_cancelled is
shared with 7 call sites in generation_jobs.py -- a generation job whose row
was deleted for an unrelated reason (e.g. a course or tenant hard-delete)
must not be silently read as "cancelled".
"""
import pytest
from sqlalchemy.exc import InvalidRequestError

from promptops_app.jobs.job_status import is_import_cancelled, is_job_cancelled
from promptops_app.repositories import job_repository
from tests.conftest import _TestSessionLocal


def test_missing_job_row_is_not_cancelled_by_default(db):
    """Default behaviour (generation_jobs.py's call shape) is unchanged."""
    assert is_job_cancelled(db, "nonexistent-job-id") is False


def test_missing_job_row_is_cancelled_for_the_import_path(db):
    """is_import_cancelled is the only caller allowed to read a purged
    (deleted) job row as a cancel -- see import_jobs.py."""
    assert is_import_cancelled(db, "nonexistent-job-id") is True


def test_row_deleted_by_another_session_raises_by_default(db):
    """Exact pre-PR behaviour: refresh() used to be called with no
    try/except at all, so a job row deleted mid-run by another session
    (e.g. the API endpoint's own session doing the purge, independent of
    the worker's) failed the job instead of letting it silently keep going.
    """
    from promptops_app.database import GenerationJob

    job_id = job_repository.create_job(db, user_name="u", request_params={})

    # Load into *this* session's identity map first (not yet expired), so
    # is_job_cancelled's own db.get() returns the cached row instead of
    # re-querying -- forcing the failure through db.refresh() itself, same
    # as it would with two independent worker sessions.
    row = db.get(GenerationJob, job_id)
    assert row is not None

    other_session = _TestSessionLocal()
    other_session.query(GenerationJob).filter(GenerationJob.id == job_id).delete()
    other_session.commit()
    other_session.close()

    with pytest.raises(InvalidRequestError):
        is_job_cancelled(db, job_id)

    db.rollback()   # clear the failed refresh's dirty state before reusing db
    assert is_import_cancelled(db, job_id) is True
