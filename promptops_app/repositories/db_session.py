"""Database session boundary for the Streamlit app."""

from promptops_app.database import SessionLocal, init_db_with_seed, init_db


def get_session():
    return SessionLocal()
