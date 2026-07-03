import { apiFetch } from './client';

export async function fetchRequests() {
  const res = await apiFetch('/api/requests');
  return res.ok ? res.json() : [];
}

export async function createRequest(body) {
  const res = await apiFetch('/api/requests', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error('Failed to submit request');
}

export async function updateRequest(id, body) {
  const res = await apiFetch(`/api/requests/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error('Update failed');
}
