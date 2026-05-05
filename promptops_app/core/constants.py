"""String constants for workflow states, user roles, and document statuses.

Using plain string constants (not Enum) keeps compatibility with the existing
ORM string columns and UI comparisons without requiring .value everywhere.

Usage:
    from promptops_app.core.constants import WorkflowState, UserRole
    if block.workflow_state == WorkflowState.IN_REVIEW: ...
"""


class WorkflowState:
    DRAFT              = "draft"
    IN_REVIEW          = "in_review"
    CHANGES_REQUESTED  = "changes_requested"
    APPROVED           = "approved"
    PUBLISHED          = "published"
    ARCHIVED           = "archived"
    REJECTED           = "rejected"

    ALL = (DRAFT, IN_REVIEW, CHANGES_REQUESTED, APPROVED, PUBLISHED, ARCHIVED, REJECTED)

    # States that permit content export (non-admin users)
    EXPORTABLE = {APPROVED, PUBLISHED}

    # States that are terminal (no further transitions except archive)
    TERMINAL   = {PUBLISHED, ARCHIVED}

    # Display labels and colour palette for UI badges
    LABELS = {
        DRAFT:             "Draft",
        IN_REVIEW:         "In Review",
        CHANGES_REQUESTED: "Changes Requested",
        APPROVED:          "Approved",
        PUBLISHED:         "Published",
        ARCHIVED:          "Archived",
        REJECTED:          "Rejected",
    }

    # (background, text) CSS colour pairs for status badges
    BADGE_COLOURS = {
        DRAFT:             ("#f1f5f9", "#475569"),   # slate
        IN_REVIEW:         ("#fffbeb", "#92400e"),   # amber
        CHANGES_REQUESTED: ("#fff7ed", "#9a3412"),   # orange
        APPROVED:          ("#f0fdf4", "#166534"),   # green
        PUBLISHED:         ("#eef2ff", "#3730a3"),   # indigo
        ARCHIVED:          ("#f3f4f6", "#4b5563"),   # gray
        REJECTED:          ("#fef2f2", "#991b1b"),   # red
    }

    ICONS = {
        DRAFT:             "📝",
        IN_REVIEW:         "🔍",
        CHANGES_REQUESTED: "🔁",
        APPROVED:          "✅",
        PUBLISHED:         "🚀",
        ARCHIVED:          "🗄️",
        REJECTED:          "❌",
    }

    @classmethod
    def label(cls, state: str) -> str:
        return cls.LABELS.get(state.lower(), state.title())

    @classmethod
    def badge_html(cls, state: str) -> str:
        """Return an inline HTML badge for a workflow state."""
        s   = (state or "draft").lower()
        lbl = cls.LABELS.get(s, s.title())
        ico = cls.ICONS.get(s, "•")
        bg, fg = cls.BADGE_COLOURS.get(s, ("#f1f5f9", "#475569"))
        return (
            f"<span style='background:{bg};color:{fg};font-size:0.72rem;"
            f"font-weight:700;padding:2px 10px;border-radius:12px;"
            f"letter-spacing:.04em;white-space:nowrap;'>{ico} {lbl}</span>"
        )


class UserRole:
    ADMIN    = "admin"
    AUTHOR   = "author"
    REVIEWER = "reviewer"

    ALL = (ADMIN, AUTHOR, REVIEWER)


class DocumentStatus:
    ACTIVE   = "active"
    ARCHIVED = "archived"


class FeedbackScope:
    ONE_TIME = "one_time"
    LEARNING = "learning"


class ChangeSource:
    """Block version change sources recorded in BlockVersion.change_source."""
    GENERATION          = "generation"
    EDIT                = "edit"
    REGENERATION        = "regeneration"
    RESTORE             = "restore"
    PRE_RESTORE_SNAPSHOT = "pre_restore_snapshot"
    MANUAL_SNAPSHOT     = "manual_snapshot"
