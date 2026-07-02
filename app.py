"""PromptOps / Content AI Studio Streamlit entry point."""

# Configure logging before any other module is imported so that all
# loggers created at module-level pick up the correct handler and level.
from promptops_app.core.logging import configure_logging
configure_logging()

from promptops_app.streamlit_app import run  # noqa: E402


if __name__ == "__main__":
    run()
