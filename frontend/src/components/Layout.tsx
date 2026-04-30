import { NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '@/lib/auth';

const sections = [
  {
    heading: 'Library',
    links: [
      { to: '/documents', label: 'Documents' },
      { to: '/results', label: 'Results' },
      { to: '/answers', label: 'Answers' },
    ],
  },
  {
    heading: 'Configuration',
    links: [
      { to: '/config/document-classes', label: 'Document classes' },
      { to: '/config/entity-types', label: 'Entity types' },
      { to: '/config/relationships', label: 'Relationships' },
      { to: '/config/dossier-classes', label: 'Dossier classes' },
      { to: '/config/playbooks', label: 'Playbooks' },
    ],
  },
  {
    heading: 'Review',
    links: [{ to: '/review/proposals', label: 'Proposals' }],
  },
  {
    heading: 'System',
    links: [{ to: '/sinas-status', label: 'Sinas integration' }],
  },
];

export function Layout() {
  const { me, signOut } = useAuth();
  return (
    <div className="min-h-screen flex">
      <aside className="w-64 border-r border-stone-200 bg-white px-4 py-6 flex flex-col">
        <div className="px-2 mb-8">
          <div className="text-lg font-semibold text-forest-700">Sinas Grove</div>
          <div className="text-xs text-stone-500">v0.1</div>
        </div>
        <div className="flex-1">
          {sections.map((section) => (
            <div key={section.heading} className="mb-6">
              <div className="px-2 text-xs font-semibold uppercase tracking-wider text-stone-400 mb-2">
                {section.heading}
              </div>
              <nav className="flex flex-col">
                {section.links.map((link) => (
                  <NavLink
                    key={link.to}
                    to={link.to}
                    className={({ isActive }) =>
                      `px-2 py-1.5 rounded text-sm ${
                        isActive
                          ? 'bg-forest-100 text-forest-700 font-medium'
                          : 'text-stone-700 hover:bg-stone-100'
                      }`
                    }
                  >
                    {link.label}
                  </NavLink>
                ))}
              </nav>
            </div>
          ))}
        </div>
        {me && (
          <div className="px-2 pt-4 border-t border-stone-200 text-xs text-stone-500">
            <div className="mb-1">
              {me.is_admin ? 'admin' : me.roles.length ? me.roles.join(', ') : 'user'} ·{' '}
              <span className="font-mono">{me.auth_mode}</span>
            </div>
            <button onClick={signOut} className="hover:text-stone-700 underline">
              Sign out
            </button>
          </div>
        )}
      </aside>
      <main className="flex-1 px-8 py-8 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
