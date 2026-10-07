/**
 * RoleGate Component
 * Controls content visibility based on user role
 */

import React from 'react';
import { useAppSelector } from '@app/hooks';

type UserRole = 'admin' | 'author' | 'reviewer' | 'user';

interface RoleGateProps {
  children: React.ReactNode;
  requiredRole: UserRole | UserRole[];
  fallback?: React.ReactNode;
}

/**
 * RoleGate wraps content that should only be visible to users with specific roles.
 * If the user doesn't have the required role, shows a fallback message.
 */
export function RoleGate({ children, requiredRole, fallback }: RoleGateProps) {
  const { user } = useAppSelector((state) => state.auth);

  // Normalize requiredRole to array
  const requiredRoles = Array.isArray(requiredRole) ? requiredRole : [requiredRole];

  // Check if user has one of the required roles
  const hasAccess = user && requiredRoles.includes(user.role as UserRole);

  if (!hasAccess) {
    return fallback ? (
      <div className="role-gate-fallback">
        {fallback}
      </div>
    ) : (
      <div className="role-gate-denied">
        <div className="role-gate-denied__message">
          <p>You don't have permission to access this feature.</p>
          <p className="role-gate-denied__detail">
            Please contact your administrator if you believe this is an error.
          </p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}

export default RoleGate;
