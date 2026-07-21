"""Reverse pipeline — Canvas IMSCC course importer (additive subsystem).

Mirror of ``promptops_app/exporters/``. All import/reconstruction logic lives
under this package (plus ``jobs/import_jobs.py`` and
``app/api/v1/routers/imports.py``). It reuses existing repositories/services in
an append/read manner and produces the SAME database rows the scratch pipeline
produces — downstream code stays origin-agnostic. See reverse_cas.md.

Nothing is wired up until the ``IMPORT_COURSES_ENABLED`` feature flag is on.
"""
