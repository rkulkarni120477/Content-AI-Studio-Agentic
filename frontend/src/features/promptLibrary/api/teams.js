import { apiFetch } from './client';

export async function fetchTeams() {
  const res = await apiFetch('/api/teams');
  return res.ok ? res.json() : [];
}

export async function createTeam(id, name) {
  const res = await apiFetch('/api/teams', {
    method: 'POST',
    body: JSON.stringify({ id, name }),
  });
  if (res.status === 409) throw new Error('Team already exists');
  if (!res.ok) throw new Error('Failed to create team');
}

export async function updateTeam(id, name) {
  const res = await apiFetch(`/api/teams/${id}`, {
    method: 'PUT',
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error('Update failed');
}

export async function deleteTeam(id) {
  const res = await apiFetch(`/api/teams/${id}`, { method: 'DELETE' });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.error || e.detail || 'Cannot delete team');
  }
}
