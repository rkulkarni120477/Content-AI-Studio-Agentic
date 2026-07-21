import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { fetchCoursesThunk, fetchWorkspaceConfigThunk } from '@features/dashboard/dashboardThunks';
import {
  selectCourses, selectSelectedProject, selectSelectedCluster,
  setSelectedProject, setSelectedCluster, setSelectedCourse,
  selectIsLoadingCourses, selectDashboardError,
} from '@features/dashboard/dashboardSlice';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import SelectionLayout from '@components/layout/SelectionLayout/SelectionLayout';
import SelectionPageHeader from '@components/streamlit/SelectionPageHeader/SelectionPageHeader';
import StreamlitCard from '@components/streamlit/StreamlitCard/StreamlitCard';
import EmptyState from '@components/common/EmptyState/EmptyState';
import EditEntityModal from '@features/dashboard/components/EditEntityModal/EditEntityModal';
import ManageUsersModal from '@features/dashboard/components/ManageUsersModal/ManageUsersModal';
import CreateCourseModal from '@features/dashboard/components/CreateCourseModal/CreateCourseModal';
import { importService } from '@features/import/services/importService';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import Loader from '@components/common/Loader/Loader';
import Button from '@components/common/Button/Button';
import { useAuth } from '@hooks/useAuth';
import { ROUTES, ROLES } from '@utils/constants';
import { extractErrorMessage } from '@utils/helpers';
import gridStyles from '@features/dashboard/styles/selectionGrid.module.scss';

export default function CoursesPage() {
  const { projectId, clusterId } = useParams();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const { hasPermission, isAdmin, role } = useAuth();
  const courses = useAppSelector(selectCourses);
  const selProj = useAppSelector(selectSelectedProject);
  const selCluster = useAppSelector(selectSelectedCluster);
  const isLoadingCourses = useAppSelector(selectIsLoadingCourses);
  const coursesError = useAppSelector(selectDashboardError);

  const [createLoading, setCreateLoading] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editModal, setEditModal] = useState(null);
  const [usersModal, setUsersModal] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [importEnabled, setImportEnabled] = useState(false);

  const pid = Number(projectId);
  const cid = Number(clusterId);
  const canEditCourse = hasPermission('course.edit');
  const canDeleteCourse = hasPermission('course.delete');
  const canAssignUsers = hasPermission('users.assign');
  const canCreate = hasPermission('course.create') || isAdmin;

  useEffect(() => {
    if (!pid || !cid) {
      navigate(ROUTES.DASHBOARD, { replace: true });
      return;
    }
    async function sync() {
      if (selProj?.id !== pid) {
        try {
          const p = await dashboardService.getProject(pid);
          dispatch(setSelectedProject(p));
        } catch {
          navigate(ROUTES.DASHBOARD, { replace: true });
          return;
        }
      }
      if (selCluster?.id !== cid) {
        try {
          const c = await dashboardService.listClusters(pid);
          const cluster = c?.items?.find((x) => x.id === cid);
          if (cluster) dispatch(setSelectedCluster(cluster));
          else navigate(ROUTES.PROJECT_CLUSTERS(pid), { replace: true });
        } catch {
          navigate(ROUTES.PROJECT_CLUSTERS(pid), { replace: true });
        }
      }
    }
    sync();
  }, [pid, cid, selProj?.id, selCluster?.id, dispatch, navigate]);

  useEffect(() => {
    if (cid) dispatch(fetchCoursesThunk(cid));
  }, [cid, dispatch]);

  // Probe the reverse-pipeline flag: /imports/health 404s when the router isn't
  // mounted (flag off), in which case Import stays disabled.
  useEffect(() => {
    let alive = true;
    importService.health()
      .then((res) => { if (alive) setImportEnabled(Boolean(res?.enabled)); })
      .catch(() => { if (alive) setImportEnabled(false); });
    return () => { alive = false; };
  }, []);

  async function handleCreate(data) {
    setCreateLoading(true);
    try {
      await dashboardService.createCourse(pid, { ...data, cluster_id: cid });
      toast.success('Course created');
      dispatch(fetchCoursesThunk(cid));
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setCreateLoading(false);
    }
  }

  function handleOpen(course) {
    dispatch(setSelectedCourse(course));
    dispatch(fetchWorkspaceConfigThunk(course.id));
    const landing = role === ROLES.AUTHOR ? ROUTES.CDD(course.id) : ROUTES.STYLE(course.id);
    navigate(landing);
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleteLoading(true);
    try {
      await dashboardService.deleteCourse(deleteTarget.id);
      toast.success('Course archived');
      setDeleteTarget(null);
      dispatch(fetchCoursesThunk(cid));
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setDeleteLoading(false);
    }
  }

  if (!selProj || !selCluster) return null;

  return (
    <SelectionLayout
      sidebarProps={{
        variant: 'course',
        projectName: selProj.name,
        clusterName: selCluster.name,
        projectId: pid,
        onBackClusters: () => navigate(ROUTES.PROJECT_CLUSTERS(pid)),
        onCreateCourse: handleCreate,
        onRequestCreateCourse: () => setCreateModalOpen(true),
        createLoading,
      }}
    >
      <SelectionPageHeader
        eyebrow={`${selProj.name} › ${selCluster.name}`}
        title="Select Course"
        subtitle="Choose a course to enter the workspace."
      />

      {isLoadingCourses ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2rem 0', color: '#64748b' }}>
          <Loader size="sm" /> Loading courses…
        </div>
      ) : courses?.items?.length ? (
        <div className={gridStyles.grid}>
          {courses?.items?.map((course) => (
            <StreamlitCard
              key={course.id}
              title={course.name}
              badge={course.source_type === 'imscc' || course.source_type === 'cendoc' ? 'Imported' : undefined}
              description={course.description}
              onOpen={() => handleOpen(course)}
              openLabel="Enter Workspace →"
              onEdit={() => setEditModal({ type: 'course', item: course })}
              onDelete={() => setDeleteTarget(course)}
              onManageUsers={() => setUsersModal({ scope: 'course', item: course })}
              canEdit={canEditCourse}
              canDelete={canDeleteCourse}
              canManageUsers={canAssignUsers}
            />
          ))}
        </div>
      ) : coursesError ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '1.5rem 0', flexWrap: 'wrap' }}>
          <span>⚠️ Couldn’t load courses.</span>
          <Button type="button" variant="ghost" onClick={() => dispatch(fetchCoursesThunk(cid))}>Try again</Button>
        </div>
      ) : (
        <EmptyState title="No courses" message={canCreate ? 'Create a course using the sidebar panel.' : 'Ask an Admin or Lead to create courses.'} />
      )}

      <CreateCourseModal
        open={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        onCreateCourse={handleCreate}
        createLoading={createLoading}
        importEnabled={importEnabled}
        onImport={() => navigate(`${ROUTES.IMPORT(pid)}?cluster_id=${cid}`)}
      />
      <EditEntityModal open={Boolean(editModal)} entityType="course" entity={editModal?.item} onClose={() => setEditModal(null)} onSaved={() => dispatch(fetchCoursesThunk(cid))} />
      <ManageUsersModal open={Boolean(usersModal)} onClose={() => setUsersModal(null)} scope="course" entityId={usersModal?.item?.id} entityName={usersModal?.item?.name} projectId={pid} />
      <ConfirmDialog open={Boolean(deleteTarget)} onClose={() => setDeleteTarget(null)} onConfirm={handleDelete} title="Delete Course" message={`Delete "${deleteTarget?.name}"? All content will be archived.`} loading={deleteLoading} />
    </SelectionLayout>
  );
}
