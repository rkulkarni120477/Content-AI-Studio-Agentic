import { useAuth } from '@hooks/useAuth';
import { ROLE_LABELS, ROLES } from '@utils/constants';
import { cn } from '@utils/helpers';
import HeaderUser from './HeaderUser';
import styles from './IdentityBar.module.scss';

const ROLE_COLORS = {
  [ROLES.ADMIN]:    '#7c3aed',
  [ROLES.REVIEWER]: '#0f766e',
  [ROLES.AUTHOR]:   '#4338ca',
};

/**
 * Full-width top header strip (bottom border) that anchors the signed-in
 * identity chip to the top of a page's main content area. Designed to be the
 * first child of a `.main` that uses the standard $space-8 / $space-10 padding;
 * it breaks out of that padding so the strip spans edge to edge.
 *
 * Pass username/roleLabel/roleColor to reuse a page's already-computed values,
 * or omit them to derive from the authenticated user.
 */
export default function IdentityBar({ username, roleLabel, roleColor, className }) {
  const { user, role } = useAuth();
  const name = username ?? user?.username;
  if (!name) return null;
  const label = roleLabel ?? ROLE_LABELS[role] ?? role ?? '';
  const color = roleColor ?? ROLE_COLORS[role] ?? '#7c3aed';

  return (
    <div className={cn(styles.identityBar, className)}>
      <HeaderUser username={name} roleLabel={label} roleColor={color} />
    </div>
  );
}
