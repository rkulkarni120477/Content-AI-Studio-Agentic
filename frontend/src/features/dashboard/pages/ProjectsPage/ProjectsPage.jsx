import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { fetchProjectsThunk } from '@features/dashboard/dashboardThunks';
import { selectProjects, setSelectedProject } from '@features/dashboard/dashboardSlice';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import SelectionLayout from '@components/layout/SelectionLayout/SelectionLayout';
import SelectionPageHeader from '@components/streamlit/SelectionPageHeader/SelectionPageHeader';
import StreamlitCard from '@components/streamlit/StreamlitCard/StreamlitCard';
import EmptyState from '@components/common/EmptyState/EmptyState';
import EditEntityModal from '@features/dashboard/components/EditEntityModal/EditEntityModal';
import ManageUsersModal from '@features/dashboard/components/ManageUsersModal/ManageUsersModal';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import { useAuth } from '@hooks/useAuth';
import { ROUTES } from '@utils/constants';
import { extractErrorMessage } from '@utils/helpers';
import gridStyles from '@features/dashboard/styles/selectionGrid.module.scss';

export default function ProjectsPage() {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const projects = useAppSelector(selectProjects);

  const [createLoading, setCreateLoading] = useState(false);
  const [editModal, setEditModal] = useState(null);
  const [usersModal, setUsersModal] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  const canEditProject   = hasPermission('project.edit') || hasPermission('users.assign');
  const canDeleteProject = hasPermission('project.delete');
  const canAssignUsers   = hasPermission('users.assign');

  useEffect(() => { dispatch(fetchProjectsThunk()); }, [dispatch]);

  async function handleCreate(data) {
    setCreateLoading(true);
    try {
      await dashboardService.createProject(data);
      toast.success('Project created');
      dispatch(fetchProjectsThunk());
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setCreateLoading(false);
    }
  }

  function handleOpen(proj) {
    dispatch(setSelectedProject(proj));
    navigate(ROUTES.PROJECT_CLUSTERS(proj.id));
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleteLoading(true);
    try {
      await dashboardService.deleteProject(deleteTarget.id);
      toast.success('Project archived');
      setDeleteTarget(null);
      dispatch(fetchProjectsThunk());
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setDeleteLoading(false);
    }
  }

  return (
    <SelectionLayout sidebarProps={{ variant: 'project', onCreateProject: handleCreate, createLoading }}>
      <SelectionPageHeader
        title="Project Dashboard"
        subtitle="Select a project to continue."
      />

      {projects?.items?.length === 0 ? (
        <EmptyState title="No projects yet" message="Create your first project using the sidebar panel (Admin)." />
      ) : (
        <div className={gridStyles.grid}>
          {projects.items.map((proj) => (
            <StreamlitCard
              key={proj.id}
              title={proj.name}
              clientLine={proj.client_name}
              description={proj.description}
              onOpen={() => handleOpen(proj)}
              onEdit={() => setEditModal({ type: 'project', item: proj })}
              onDelete={() => setDeleteTarget(proj)}
              onManageUsers={() => setUsersModal({ scope: 'project', item: proj })}
              canEdit={canEditProject}
              canDelete={canDeleteProject}
              canManageUsers={canAssignUsers}
            />
          ))}
        </div>
      )}

      <EditEntityModal open={Boolean(editModal)} entityType="project" entity={editModal?.item} onClose={() => setEditModal(null)} onSaved={() => dispatch(fetchProjectsThunk())} />
      <ManageUsersModal open={Boolean(usersModal)} onClose={() => setUsersModal(null)} scope="project" entityId={usersModal?.item?.id} entityName={usersModal?.item?.name} />
      <ConfirmDialog open={Boolean(deleteTarget)} onClose={() => setDeleteTarget(null)} onConfirm={handleDelete} title="Delete Project" message={`Delete "${deleteTarget?.name}"? This will archive the project and its courses.`} loading={deleteLoading} />
    </SelectionLayout>
  );
}
