import { useEffect } from 'react';
import { Outlet, useParams, Navigate } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  selectSelectedCourse,
  setSelectedCourse, setSelectedProject, setSelectedCluster,
} from '@features/dashboard/dashboardSlice';
import { fetchWorkspaceConfigThunk } from '@features/dashboard/dashboardThunks';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import { resetWorkspaceContent } from '@app/resetWorkspaceContent';
import WorkspaceSidebar from '../WorkspaceSidebar/WorkspaceSidebar';
import styles from './WorkspaceLayout.module.scss';

export default function WorkspaceLayout() {
  const { courseId } = useParams();
  const dispatch = useAppDispatch();
  const selCourse = useAppSelector(selectSelectedCourse);
  const cid = Number(courseId);

  // Isolate content per title: whenever the active course changes, drop any
  // transient content held in Redux from the previously open title. Runs only on
  // course change (not same-course tab switches), and clears synchronously while
  // each page's own mount fetches resolve later — so it never clobbers the new
  // title's incoming data.
  useEffect(() => {
    if (!cid || Number.isNaN(cid)) return;
    dispatch(resetWorkspaceContent());
  }, [cid, dispatch]);

  useEffect(() => {
    if (!cid) return;
    if (selCourse?.id === cid) {
      dispatch(fetchWorkspaceConfigThunk(cid));
      return;
    }
    async function loadCourse() {
      try {
        const course = await dashboardService.getCourse(cid);
        if (course.project_id) {
          const p = await dashboardService.getProject(course.project_id);
          dispatch(setSelectedProject(p));
        }
        if (course.project_id && course.cluster_id) {
          const cl = await dashboardService.listClusters(course.project_id);
          const cluster = cl?.items?.find((x) => x.id === course.cluster_id);
          if (cluster) dispatch(setSelectedCluster(cluster));
        }
        dispatch(setSelectedCourse(course));
        dispatch(fetchWorkspaceConfigThunk(cid));
      } catch {
        /* redirect handled below */
      }
    }
    loadCourse();
  }, [cid, selCourse?.id, dispatch]);

  if (!courseId || Number.isNaN(cid)) {
    return <Navigate to="/dashboard" replace />;
  }

  return (
    <div className={styles.layout}>
      <WorkspaceSidebar />
      <div className={styles.main}>
        <Outlet />
      </div>
      <Toaster position="top-right" toastOptions={{ duration: 4000 }} />
    </div>
  );
}
