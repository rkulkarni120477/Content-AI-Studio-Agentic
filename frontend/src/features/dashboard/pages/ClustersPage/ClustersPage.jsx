import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { fetchClustersThunk } from '@features/dashboard/dashboardThunks';
import {
  selectClusters, selectSelectedProject, setSelectedProject, setSelectedCluster,
  selectIsLoadingClusters, selectDashboardError,
} from '@features/dashboard/dashboardSlice';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import SelectionLayout from '@components/layout/SelectionLayout/SelectionLayout';
import SelectionPageHeader from '@components/streamlit/SelectionPageHeader/SelectionPageHeader';
import StreamlitCard from '@components/streamlit/StreamlitCard/StreamlitCard';
import EmptyState from '@components/common/EmptyState/EmptyState';
import EditEntityModal from '@features/dashboard/components/EditEntityModal/EditEntityModal';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import ClusterPromptManager from '@components/cluster/ClusterPromptManager/ClusterPromptManager';
import Button from '@components/common/Button/Button';
import Loader from '@components/common/Loader/Loader';
import { useAuth } from '@hooks/useAuth';
import { ROUTES } from '@utils/constants';
import { extractErrorMessage } from '@utils/helpers';
import gridStyles from '@features/dashboard/styles/selectionGrid.module.scss';
import pageStyles from './ClustersPage.module.scss';

export default function ClustersPage() {
  const { projectId } = useParams();
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const { hasPermission, isAdmin } = useAuth();
  const clusters = useAppSelector(selectClusters);
  const selProj = useAppSelector(selectSelectedProject);
  const isLoadingClusters = useAppSelector(selectIsLoadingClusters);
  const clustersError = useAppSelector(selectDashboardError);

  const [createLoading, setCreateLoading] = useState(false);
  const [editModal, setEditModal] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [cpMgrOpen, setCpMgrOpen] = useState(false);

  const pid = Number(projectId);
  const canManage = hasPermission('course.create') || isAdmin;
  const canDelete = hasPermission('project.delete');

  useEffect(() => {
    if (!pid) {
      navigate(ROUTES.DASHBOARD, { replace: true });
      return;
    }
    async function syncProject() {
      if (selProj?.id === pid) return;
      try {
        const p = await dashboardService.getProject(pid);
        dispatch(setSelectedProject(p));
      } catch {
        navigate(ROUTES.DASHBOARD, { replace: true });
      }
    }
    syncProject();
  }, [pid, selProj?.id, dispatch, navigate]);

  useEffect(() => {
    if (pid) dispatch(fetchClustersThunk(pid));
  }, [pid, dispatch]);

  async function handleCreate(data) {
    setCreateLoading(true);
    try {
      await dashboardService.createCluster(pid, data);
      toast.success('Category created');
      dispatch(fetchClustersThunk(pid));
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setCreateLoading(false);
    }
  }

  function handleOpen(cluster) {
    dispatch(setSelectedCluster(cluster));
    navigate(ROUTES.CLUSTER_COURSES(pid, cluster.id));
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleteLoading(true);
    try {
      await dashboardService.deleteCluster(deleteTarget.id);
      toast.success('Category deleted');
      setDeleteTarget(null);
      dispatch(fetchClustersThunk(pid));
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setDeleteLoading(false);
    }
  }

  if (!selProj) return null;

  return (
    <SelectionLayout
      sidebarProps={{
        variant: 'cluster',
        projectName: selProj.name,
        onCreateCluster: handleCreate,
        createLoading,
      }}
    >
      <div className={pageStyles.headerRow}>
        <SelectionPageHeader
          eyebrow={`Project: ${selProj.name}`}
          title="Select Category"
          subtitle="Choose a domain category to browse its titles."
        />
        {canManage && (
          <Button
            type="button"
            variant="primary"
            className={pageStyles.cpBtn}
            onClick={() => setCpMgrOpen((v) => !v)}
          >
            ➕ Category Prompt
          </Button>
        )}
      </div>

      {cpMgrOpen && canManage && (
        <ClusterPromptManager clusters={clusters?.items || []} />
      )}

      {isLoadingClusters ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2rem 0', color: '#64748b' }}>
          <Loader size="sm" /> Loading categories…
        </div>
      ) : clusters?.items?.length ? (
        <div className={gridStyles.grid}>
          {clusters?.items?.map((cluster) => (
            <StreamlitCard
              key={cluster.id}
              title={`🗂️ ${cluster.name}`}
              description={cluster.description}
              footerLine={`${cluster.course_count ?? 0} title${cluster.course_count !== 1 ? 's' : ''}`}
              onOpen={() => handleOpen(cluster)}
              onEdit={() => setEditModal({ type: 'cluster', item: cluster })}
              onDelete={() => setDeleteTarget(cluster)}
              canEdit={canManage}
              canDelete={canDelete}
            />
          ))}
        </div>
      ) : clustersError ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '1.5rem 0', flexWrap: 'wrap' }}>
          <span>⚠️ Couldn’t load categories.</span>
          <Button type="button" variant="ghost" onClick={() => dispatch(fetchClustersThunk(pid))}>Try again</Button>
        </div>
      ) : (
        <EmptyState title="No categories" message="Create a category using the sidebar panel." />
      )}

      <EditEntityModal open={Boolean(editModal)} entityType="cluster" entity={editModal?.item} onClose={() => setEditModal(null)} onSaved={() => dispatch(fetchClustersThunk(pid))} />
      <ConfirmDialog open={Boolean(deleteTarget)} onClose={() => setDeleteTarget(null)} onConfirm={handleDelete} title="Delete Category" message={`Delete "${deleteTarget?.name}"? Categories with active titles cannot be deleted.`} loading={deleteLoading} />
    </SelectionLayout>
  );
}
