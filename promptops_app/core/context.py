from dataclasses import dataclass, field


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
    # Cluster layer — defaults keep all existing PageContext construction sites valid
    cluster_id: int = 0
    cluster_name: str = ""
