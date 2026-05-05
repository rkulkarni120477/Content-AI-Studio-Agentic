from dataclasses import dataclass


@dataclass(frozen=True)
class PageContext:
    user_role: str
    user_name: str
    project_id: int
    project_name: str
    course_id: int
    course_name: str
    is_admin: bool
    is_lead: bool
    model_choice: str
    expert_domain: str
    target_audience: str
    audience_category: str
