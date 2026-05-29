"""Cluster Repository — Cluster database access."""

from __future__ import annotations

from promptops_app.database import Cluster, Course


def list_clusters_for_project(db, project_id: int):
    return (
        db.query(Cluster)
        .filter(Cluster.project_id == project_id, Cluster.is_active == True)  # noqa: E712
        .order_by(Cluster.created_at.asc())
        .all()
    )


def get_cluster_by_id(db, cluster_id: int):
    return (
        db.query(Cluster)
        .filter(Cluster.id == cluster_id, Cluster.is_active == True)  # noqa: E712
        .first()
    )


def count_courses_for_cluster(db, cluster_id: int) -> int:
    return (
        db.query(Course)
        .filter(Course.cluster_id == cluster_id, Course.is_active == True)  # noqa: E712
        .count()
    )
