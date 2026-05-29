import { useEffect, useState } from 'react';
import Modal from '@components/common/Modal/Modal';
import Button from '@components/common/Button/Button';
import Loader from '@components/common/Loader/Loader';
import { dashboardService } from '@features/dashboard/services/dashboardService';
import { extractErrorMessage } from '@utils/helpers';
import styles from './ManageUsersModal.module.scss';

export default function ManageUsersModal({
  open,
  onClose,
  scope,
  entityId,
  entityName,
  projectId,
}) {
  const [allUsers, setAllUsers]       = useState([]);
  const [assigned, setAssigned]       = useState(new Set());
  const [projectUsers, setProjectUsers] = useState(new Set());
  const [loading, setLoading]         = useState(false);
  const [toggling, setToggling]       = useState(null);
  const [error, setError]             = useState(null);

  useEffect(() => {
    if (!open || !entityId) return;
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const usersRes = await dashboardService.listUsers();
        const nonAdmin = (usersRes?.items ?? []).filter((u) => u.role !== 'admin' && u.is_active);
        const assignedRes = scope === 'project'
          ? await dashboardService.listProjectUsers(entityId)
          : await dashboardService.listCourseUsers(entityId);

        let projAssigned = new Set();
        if (scope === 'course' && projectId) {
          const projRes = await dashboardService.listProjectUsers(projectId);
          projAssigned = new Set((projRes ?? []).map((r) => r.username));
        }

        if (!cancelled) {
          setAllUsers(nonAdmin);
          setAssigned(new Set((assignedRes ?? []).map((r) => r.username)));
          setProjectUsers(projAssigned);
        }
      } catch (e) {
        if (!cancelled) setError(extractErrorMessage(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => { cancelled = true; };
  }, [open, entityId, scope, projectId]);

  async function handleToggle(username, isChecked) {
    setToggling(username);
    setError(null);
    try {
      if (isChecked) {
        if (scope === 'project') {
          await dashboardService.assignProjectUser(entityId, username);
        } else {
          await dashboardService.assignCourseUser(entityId, username);
        }
        setAssigned((prev) => new Set([...prev, username]));
      } else {
        if (scope === 'project') {
          await dashboardService.unassignProjectUser(entityId, username);
        } else {
          await dashboardService.unassignCourseUser(entityId, username);
        }
        setAssigned((prev) => {
          const next = new Set(prev);
          next.delete(username);
          return next;
        });
      }
    } catch (e) {
      setError(extractErrorMessage(e));
    } finally {
      setToggling(null);
    }
  }

  const title = scope === 'project'
    ? `Manage Users — ${entityName}`
    : `Manage Users — ${entityName}`;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      footer={<Button variant="ghost" onClick={onClose}>Close</Button>}
    >
      {loading ? (
        <Loader />
      ) : (
        <>
          {scope === 'course' && (
            <p className={styles.hint}>
              Users assigned to the project are marked. Check to grant course-level access.
            </p>
          )}
          {error && <p className={styles.error} role="alert">{error}</p>}
          {allUsers.length === 0 ? (
            <p className={styles.empty}>No non-admin users in the system.</p>
          ) : (
            <ul className={styles.list}>
              {allUsers.map((u) => {
                const checked = assigned.has(u.username);
                const inProject = projectUsers.has(u.username);
                return (
                  <li key={u.username} className={styles.row}>
                    <label className={styles.label}>
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={toggling === u.username}
                        onChange={(e) => handleToggle(u.username, e.target.checked)}
                      />
                      <span>
                        {u.username}
                        {u.role_display ? ` (${u.role_display})` : ` (${u.role})`}
                        {scope === 'course' && inProject && (
                          <span className={styles.badge}> project</span>
                        )}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </Modal>
  );
}
