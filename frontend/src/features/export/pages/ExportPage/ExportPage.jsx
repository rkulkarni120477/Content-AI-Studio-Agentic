import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useAppSelector } from '@app/hooks';
import { selectSelectedCourse, selectSelectedProject } from '@features/dashboard/dashboardSlice';
import { api } from '@services/apiClient';
import { PROJECTS } from '@services/endpoints';
import PageContainer from '@components/layout/PageContainer/PageContainer';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import PublishedTocPanel from '@features/export/components/PublishedTocPanel/PublishedTocPanel';
import styles from './ExportPage.module.scss';

function normalizeList(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.items)) return data.items;
  return [];
}

export default function ExportPage() {
  const { courseId: routeCourseId } = useParams();
  const workspaceCourseId = routeCourseId ? Number(routeCourseId) : null;
  const selCourse = useAppSelector(selectSelectedCourse);
  const selProject = useAppSelector(selectSelectedProject);
  const [projectCourses, setProjectCourses] = useState([]);

  const activeCourseId = workspaceCourseId ?? selCourse?.id ?? null;
  const activeCourseName = selCourse?.name ?? null;

  useEffect(() => {
    const projId = selProject?.id ?? selCourse?.project_id;
    if (!projId) {
      setProjectCourses([]);
      return;
    }
    api.get(PROJECTS.COURSES(projId))
      .then((res) => setProjectCourses(normalizeList(res)))
      .catch(() => setProjectCourses([]));
  }, [selProject?.id, selCourse?.project_id]);

  return (
    <PageContainer title="" breadcrumbs={[{ label: 'Export' }]} noPadding>
      <div className={styles.page}>
        <SectionBadge
          icon="📦"
          title="Course Export"
          subtitle="Reorder the published table of contents and export an IMS Common Cartridge package for Canvas, Moodle, or Blackboard."
        />
        <PublishedTocPanel
          courseId={activeCourseId}
          courseName={activeCourseName}
          projectCourses={projectCourses}
        />
      </div>
    </PageContainer>
  );
}
