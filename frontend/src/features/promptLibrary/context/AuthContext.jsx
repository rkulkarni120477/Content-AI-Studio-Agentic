// Prompt Library auth adapter.
//
// The standalone app owned its own auth via React Context. Here it is replaced by
// a thin adapter over the host's Redux auth (@hooks/useAuth), exposing the same
// `useAuth()` shape the ported pages expect: `{ user, loading }` where `user` has
// { username, display_name, role, permissions, team }. Login/logout/tenant are
// dropped — the host chrome owns them.
import { useAuth as useHostAuth } from '@hooks/useAuth';

export function useAuth() {
  const { user, role, isLoading } = useHostAuth();
  const mapped = user
    ? {
        username: user.username,
        display_name: user.display_name || user.username,
        role: user.role || role,
        permissions: user.permissions || [],
        team: user.team ?? null,
      }
    : null;
  return { user: mapped, loading: isLoading };
}

// No-op provider kept so any incidental import resolves; the host provides auth.
export function AuthProvider({ children }) {
  return children;
}
