import { apiFetch } from './client';

export async function fetchStaffReviews(hasFeedbackOnly = false) {
  const qs = hasFeedbackOnly ? '?has_feedback=1' : '';
  const res = await apiFetch(`/api/reviews${qs}`);
  return res.ok ? res.json() : [];
}
