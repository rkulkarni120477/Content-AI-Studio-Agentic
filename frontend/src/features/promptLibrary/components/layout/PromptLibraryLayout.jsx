// Prompts section shell — integrated Studio chrome.
//
// Hosts the same SelectionSidebar as the Category screen (workspace pill,
// New Category, collapsible Prompts group) so navigating into a prompt
// destination keeps the exact sidebar the user came from, rather than a
// section-specific lookalike. Page content keeps the feature's scoped
// `.pl-root` styles.
import { useState } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import toast, { Toaster } from 'react-hot-toast';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import { fetchClustersThunk } from '@features/dashboard/dashboardThunks';
import { selectSelectedProject } from '@features/dashboard/dashboardSlice';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import SelectionSidebar from '@components/layout/SelectionLayout/SelectionSidebar';
import IdentityBar from '@components/common/HeaderUser/IdentityBar';
import { ROUTES } from '@utils/constants';
import { cn, extractErrorMessage } from '@utils/helpers';
import { ToastProvider } from '../../context/ToastContext';
import PageView from './PageView';
import styles from './PromptLibraryLayout.module.scss';
import '../../styles/promptLibrary.scss';

export default function PromptLibraryLayout() {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  // Carried over from the Category screen; null on a hard refresh / deep
  // link, in which case the sidebar simply omits the project-scoped bits.
  const selProj = useAppSelector(selectSelectedProject);
  const [createLoading, setCreateLoading] = useState(false);

  // Same behavior as ClustersPage's handler so "New Category" keeps working
  // while browsing prompt pages.
  async function handleCreateCluster(data) {
    setCreateLoading(true);
    try {
      await dashboardService.createCluster(selProj.id, data);
      toast.success('Category created');
      dispatch(fetchClustersThunk(selProj.id));
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setCreateLoading(false);
    }
  }

  return (
    <div className={styles.layout}>
      <SelectionSidebar
        variant="cluster"
        projectName={selProj?.name}
        projectId={selProj?.id}
        onCreateCluster={selProj ? handleCreateCluster : undefined}
        // Return into the CAS Category/Course flow for the project we came from.
        onBackClusters={selProj ? () => navigate(ROUTES.PROJECT_CLUSTERS(selProj.id)) : undefined}
        createLoading={createLoading}
      />

      {/* `.pl-root` scopes the ported feature CSS (incl. its element reset) to
          the content pane only — the sidebar is host chrome and must stay out. */}
      <main className={cn(styles.main, 'pl-root')}>
        <IdentityBar />
        <div className={styles.mainBody}>
          <ToastProvider>
            <div className={styles.content}>
              <PageView>
                <Outlet />
              </PageView>
            </div>
          </ToastProvider>
        </div>
      </main>

      <Toaster position="top-right" toastOptions={{ duration: 4000 }} />
    </div>
  );
}
