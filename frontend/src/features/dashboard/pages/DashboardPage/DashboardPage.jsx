import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  fetchProjectsThunk, fetchClustersThunk, fetchCoursesThunk, fetchWorkspaceConfigThunk,
} from '@features/dashboard/dashboardThunks';
import {
  selectProjects, selectClusters, selectCourses,
  selectSelectedProject, selectSelectedCluster, selectSelectedCourse,
  setSelectedProject, setSelectedCluster, setSelectedCourse,
} from '@features/dashboard/dashboardSlice';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import Button from '@components/common/Button/Button';
import styles from './DashboardPage.module.scss';

export default function DashboardPage() {
  const dispatch    = useAppDispatch();
  const navigate    = useNavigate();
  const projects    = useAppSelector(selectProjects);
  const clusters    = useAppSelector(selectClusters);
  const courses     = useAppSelector(selectCourses);
  const selProj     = useAppSelector(selectSelectedProject);
  const selCluster  = useAppSelector(selectSelectedCluster);
  const selCourse   = useAppSelector(selectSelectedCourse);

  useEffect(() => { dispatch(fetchProjectsThunk()); }, [dispatch]);

  useEffect(() => {
    if (selProj?.id) dispatch(fetchClustersThunk(selProj.id));
  }, [selProj, dispatch]);

  useEffect(() => {
    if (selCluster?.id) dispatch(fetchCoursesThunk(selCluster.id));
  }, [selCluster, dispatch]);

  function handleProjectSelect(project) {
    dispatch(setSelectedProject(project));
  }

  function handleClusterSelect(cluster) {
    dispatch(setSelectedCluster(cluster));
  }

  function handleCourseSelect(course) {
    dispatch(setSelectedCourse(course));
    dispatch(fetchWorkspaceConfigThunk(course.id));
    navigate(`/workspace/${course.id}/cdd`);
  }

  return (
    <PageContainer title="Dashboard" breadcrumbs={[{ label: 'Dashboard' }]}>
      <div className={styles.dashboard}>
        {/* Projects Panel */}
        <section className={styles.panel}>
          <div className={styles.panel__header}>
            <h2 className={styles.panel__title}>Projects</h2>
          </div>
          <div className={styles.panel__list}>
            {projects?.items?.length === 0 ? (
              <EmptyState title="No projects yet" message="Contact your admin to get access to a project." />
            ) : (
              projects?.items?.map((proj) => (
                <button
                  key={proj.id}
                  className={`${styles.card} ${selProj?.id === proj.id ? styles['card--active'] : ''}`}
                  onClick={() => handleProjectSelect(proj)}
                  type="button"
                >
                  <span className={styles.card__icon} aria-hidden="true">📁</span>
                  <div className={styles.card__info}>
                    <span className={styles.card__name}>{proj.name}</span>
                    {proj.client_name && (
                      <span className={styles.card__meta}>{proj.client_name}</span>
                    )}
                  </div>
                  {selProj?.id === proj.id && (
                    <span className={styles.card__check} aria-hidden="true">✓</span>
                  )}
                </button>
              ))
            )}
          </div>
        </section>

        {/* Clusters Panel */}
        {selProj && (
          <section className={styles.panel}>
            <div className={styles.panel__header}>
              <h2 className={styles.panel__title}>
                Categories — <span className={styles.panel__subtitle}>{selProj.name}</span>
              </h2>
            </div>
            <div className={styles.panel__list}>
              {clusters?.items?.length === 0 ? (
                <EmptyState title="No categories" message="No categories found in this project." />
              ) : (
                clusters?.items?.map((cluster) => (
                  <button
                    key={cluster.id}
                    className={`${styles.card} ${selCluster?.id === cluster.id ? styles['card--active'] : ''}`}
                    onClick={() => handleClusterSelect(cluster)}
                    type="button"
                  >
                    <span className={styles.card__icon} aria-hidden="true">🗂️</span>
                    <div className={styles.card__info}>
                      <span className={styles.card__name}>{cluster.name}</span>
                      {cluster.description && (
                        <span className={styles.card__meta}>{cluster.description}</span>
                      )}
                      {cluster.course_count != null && (
                        <span className={styles.card__meta}>
                          {cluster.course_count} course{cluster.course_count !== 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                    {selCluster?.id === cluster.id && (
                      <span className={styles.card__check} aria-hidden="true">✓</span>
                    )}
                  </button>
                ))
              )}
            </div>
          </section>
        )}

        {/* Courses Panel */}
        {selCluster && (
          <section className={styles.panel}>
            <div className={styles.panel__header}>
              <h2 className={styles.panel__title}>
                Courses — <span className={styles.panel__subtitle}>{selCluster.name}</span>
              </h2>
            </div>
            <div className={styles.panel__list}>
              {courses?.items?.length === 0 ? (
                <EmptyState title="No courses" message={`No courses found in ${selCluster.name}.`} />
              ) : (
                courses?.items?.map((course) => (
                  <button
                    key={course.id}
                    className={`${styles.card} ${selCourse?.id === course.id ? styles['card--active'] : ''}`}
                    onClick={() => handleCourseSelect(course)}
                    type="button"
                  >
                    <span className={styles.card__icon} aria-hidden="true">📚</span>
                    <div className={styles.card__info}>
                      <span className={styles.card__name}>{course.name}</span>
                    </div>
                    <Button variant="primary" size="sm" onClick={(e) => { e.stopPropagation(); handleCourseSelect(course); }}>
                      Open
                    </Button>
                  </button>
                ))
              )}
            </div>
          </section>
        )}
      </div>
    </PageContainer>
  );
}
