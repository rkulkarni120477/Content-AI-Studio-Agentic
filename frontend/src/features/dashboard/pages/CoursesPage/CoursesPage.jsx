import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { fetchCoursesThunk } from '@features/dashboard/dashboardThunks';
import {
  selectCourses, selectSelectedProject, selectSelectedCluster,
  setSelectedProject, setSelectedCluster, setSelectedCourse,
  selectIsLoadingCourses, selectCoursesLoadedFor, selectDashboardError,
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
import { useLabels } from '@hooks/useLabels';
import { ROUTES, ROLES, projectHomeRoute } from '@utils/constants';
import { extractErrorMessage } from '@utils/helpers';
import gridStyles from '@features/dashboard/styles/selectionGrid.module.scss';

export default function CoursesPage() {
  const { projectId, clusterId } = useParams();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const { hasPermission, isAdmin, role, isPlatformAdmin, projectId: authProjectId } = useAuth();
  const L = useLabels();
  const courses = useAppSelector(selectCourses);
  const selProj = useAppSelector(selectSelectedProject);
  const selCluster = useAppSelector(selectSelectedCluster);
  const isLoadingCourses = useAppSelector(selectIsLoadingCourses);
  const coursesLoadedFor = useAppSelector(selectCoursesLoadedFor);
  const coursesError = useAppSelector(selectDashboardError);

  const [createLoading, setCreateLoading] = useState(false);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [editModal, setEditModal] = useState(null);
  const [usersModal, setUsersModal] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [purgeTarget, setPurgeTarget] = useState(null);
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
      navigate(projectHomeRoute(isPlatformAdmin, authProjectId), { replace: true });
      return;
    }
    async function sync() {
      if (selProj?.id !== pid) {
        try {
          const p = await dashboardService.getProject(pid);
          dispatch(setSelectedProject(p));
        } catch {
          navigate(projectHomeRoute(isPlatformAdmin, authProjectId), { replace: true });
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
  }, [pid, cid, selProj?.id, selCluster?.id, dispatch, navigate, isPlatformAdmin, authProjectId]);

  // Fetch only once the cluster is the selected one. Firing on mount instead
  // races the sync effect above: on a cluster A -> B navigation, `courses`
  // still holds A's items (setSelectedCluster no longer clears them) and
  // isLoadingCourses is still false for the paint before this effect's
  // pending action lands, so the page would render A's titles under B's
  // header. Keyed on selCluster?.id, this runs after the cluster pointer
  // actually points at cid.
  useEffect(() => {
    if (cid && selCluster?.id === cid) dispatch(fetchCoursesThunk(cid));
  }, [cid, selCluster?.id, dispatch]);

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
      toast.success(`${L.title} created`);
      dispatch(fetchCoursesThunk(cid));
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setCreateLoading(false);
    }
  }

  function handleOpen(course) {
    dispatch(setSelectedCourse(course));
    // WorkspaceLayout hydrates sidebar config from the JWT once the course
    // route mounts. Fetching here too raced Apply and could reset the model.
    const landing = role === ROLES.AUTHOR ? ROUTES.CDD(course.id) : ROUTES.STYLE(course.id);
    navigate(landing);
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleteLoading(true);
    try {
      await dashboardService.deleteCourse(deleteTarget.id);
      toast.success(`${L.title} archived`);
      setDeleteTarget(null);
      dispatch(fetchCoursesThunk(cid));
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setDeleteLoading(false);
    }
  }

  async function handlePermanentDelete() {
    if (!purgeTarget) return;
    setDeleteLoading(true);
    try {
      await dashboardService.permanentlyDeleteCourse(purgeTarget.id);
      toast.success(`${L.title} permanently deleted`);
      setPurgeTarget(null);
      dispatch(fetchCoursesThunk(cid));
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setDeleteLoading(false);
    }
  }

  function courseBadge(course) {
    if (course.is_active === false) return 'Archived';
    if (course.source_type === 'imscc' || course.source_type === 'cendoc') return 'Imported';
    return undefined;
  }

  if (!selProj || !selCluster) return <Loader size="xl" overlay />;

  // No courses fetch for THIS cluster has settled yet, so nothing is known
  // about whether it has titles yet — `items: []` means both "not loaded" and
  // "none exist", and isLoadingCourses is still false for the paint between
  // selecting the cluster and the fetch's pending action. The `!coursesError`
  // escape hatch keeps a genuinely failed fetch from spinning forever.
  const coursesPending = isLoadingCourses
    || (coursesLoadedFor !== cid && !coursesError);

  // Render only what the marker says was fetched for THIS cluster. `state.error`
  // is shared by every dashboard fetch, so an unrelated failure can already be
  // set on arrival and trip the escape hatch above before this cluster's own
  // fetch has settled — and the grid branch below is evaluated before the error
  // branch. Reading `courses.items` directly there would paint the previous
  // cluster's titles under this one's header.
  const courseItems = coursesLoadedFor === cid ? (courses?.items ?? []) : [];

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
        title={`Select ${L.title}`}
        subtitle={`Choose a ${L.titleLower} to enter the workspace.`}
      />

      {coursesPending ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2rem 0', color: '#64748b' }}>
          <Loader size="sm" /> Loading {L.titlesLower}…
        </div>
      ) : courseItems.length ? (
        <div className={gridStyles.grid}>
          {courseItems.map((course) => {
            const archived = course.is_active === false;
            return (
              <StreamlitCard
                key={course.id}
                title={course.name}
                badge={courseBadge(course)}
                description={course.description}
                descriptionPlaceholder="No description provided."
                onOpen={archived ? undefined : () => handleOpen(course)}
                openLabel="Enter Workspace →"
                hideOpen={archived}
                onEdit={archived ? undefined : () => setEditModal({ type: 'course', item: course })}
                onDelete={() => (archived ? setPurgeTarget(course) : setDeleteTarget(course))}
                deleteLabel={archived ? '🗑️ Permanently delete' : '🗑️ Delete'}
                onManageUsers={archived ? undefined : () => setUsersModal({ scope: 'course', item: course })}
                canEdit={canEditCourse && !archived}
                canDelete={canDeleteCourse}
                canManageUsers={canAssignUsers && !archived}
              />
            );
          })}
        </div>
      ) : coursesError ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '1.5rem 0', flexWrap: 'wrap' }}>
          <span>⚠️ Couldn’t load {L.titlesLower}.</span>
          <Button type="button" variant="ghost" onClick={() => dispatch(fetchCoursesThunk(cid))}>Try again</Button>
        </div>
      ) : (
        <EmptyState
          title={`No ${L.titlesLower}`}
          message={canCreate
            ? `Create a ${L.titleLower} using the sidebar panel.`
            : `Ask an Admin or Lead to create ${L.titlesLower}.`}
        />
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
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        title={`Archive ${L.title}`}
        message={`Archive "${deleteTarget?.name}"? You can permanently delete it later from the archived list.`}
        loading={deleteLoading}
      />
      <ConfirmDialog
        open={Boolean(purgeTarget)}
        onClose={() => setPurgeTarget(null)}
        onConfirm={handlePermanentDelete}
        title={`Permanently Delete ${L.title}`}
        message={`Permanently delete "${purgeTarget?.name}" and all of its content (modules, generations, blocks, ${L.cdd}, ${L.blueprintsLower}, imports)? This cannot be undone.`}
        loading={deleteLoading}
      />
    </SelectionLayout>
  );
}
