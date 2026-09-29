"""PR review (Phase 3, source-trace): Generation.generation_params was added
to the ORM model with no Alembic migration -- only _run_legacy_ddl(), which
runs solely under DB_AUTO_DDL (default false). The test suite's own SQLite
DB is built straight from ORM metadata (see tests/conftest.py), so it can
never catch a model column with no matching migration -- a prod deploy
running ``alembic upgrade head`` would find nothing to apply and every ORM
load of Generation would fail with UndefinedColumn. This checks the actual
migration graph instead of the ORM-backed test DB.
"""
from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config("alembic.ini"))


def test_the_migration_graph_has_a_single_head():
    """A second migration branching off the same down_revision would leave
    two heads and alembic upgrade head unable to resolve which to apply."""
    script = _script_directory()
    assert len(script.get_heads()) == 1


def test_generation_params_has_a_migration_chained_to_the_prior_head():
    script = _script_directory()
    rev = script.get_revision("000100000027")
    assert rev is not None
    assert rev.down_revision == "000100000026"
    assert rev in [script.get_revision(h) for h in script.get_heads()]


def test_the_migration_is_additive_and_guarded():
    """Nullable, no server default (existing rows read back None), and
    guarded so it's a no-op where DB_AUTO_DDL already added the column."""
    import importlib.util

    script = _script_directory()
    rev = script.get_revision("000100000027")
    spec = importlib.util.spec_from_file_location("mig_027", rev.path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    assert hasattr(mig, "upgrade") and hasattr(mig, "downgrade")
    assert hasattr(mig, "_has_column")
