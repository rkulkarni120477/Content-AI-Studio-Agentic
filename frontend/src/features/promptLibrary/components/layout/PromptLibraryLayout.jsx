// Prompt Library sub-shell — ported from the standalone AppLayout.
//
// Kept: the intra-feature top nav (Library / Requests / Reviews / Audit) and the
// "New Prompt" header action, so users can move between the feature's pages under
// the single host "Prompt Library" sidebar tab. Dropped: global chrome (logout,
// user/role badges) — the host sidebar owns identity. Everything is wrapped in a
// `.pl-root` element so the ported CSS is scoped and cannot leak into the host.
import { useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { ToastProvider } from '../../context/ToastContext';
import { MAIN_NAV, visibleHeaderActions } from '../../utils/nav';
import { plHome } from '../../paths';
import PageView from './PageView';
import '../../styles/promptLibrary.scss';

export default function PromptLibraryLayout() {
  const { user } = useAuth();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const navItems = MAIN_NAV.filter((item) => item.visible(user));
  const headerActions = visibleHeaderActions(user, location.pathname);

  function closeMenu() {
    setMenuOpen(false);
  }

  return (
    <div className="pl-root">
      <ToastProvider>
        <div className={`app-shell${menuOpen ? ' menu-open' : ''}`}>
          <header className="app-header">
            <div className="app-header-inner layout-container">
              <Link to={plHome} className="logo" style={{ textDecoration: 'none' }} onClick={closeMenu}>
                Prompt<span>Library</span>
              </Link>
              <button
                type="button"
                className="header-menu-btn"
                aria-label={menuOpen ? 'Close menu' : 'Open menu'}
                aria-expanded={menuOpen}
                onClick={() => setMenuOpen((open) => !open)}
              >
                {menuOpen ? '✕' : '☰'}
              </button>
              <div className={`header-right${menuOpen ? ' is-open' : ''}`}>
                <nav className="header-nav">
                  {navItems.map((item) => (
                    <NavLink
                      key={`${item.to}-${item.label}`}
                      to={item.to}
                      end={item.end}
                      className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
                      onClick={closeMenu}
                    >
                      {item.label}
                    </NavLink>
                  ))}
                </nav>
                <div className="header-meta">
                  {headerActions.map((action) => (
                    <Link key={action.to} to={action.to} className={action.className} onClick={closeMenu}>
                      {action.label}
                    </Link>
                  ))}
                </div>
              </div>
            </div>
          </header>

          <main className="app-main">
            <PageView>
              <Outlet />
            </PageView>
          </main>

          <footer className="app-footer">
            <div className="app-footer-inner layout-container">
              <span>Prompt Library</span>
            </div>
          </footer>
        </div>
      </ToastProvider>
    </div>
  );
}
