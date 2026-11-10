import { useEffect, useState } from "react";
import { NavLink, Navigate, Outlet, useLocation } from "react-router-dom";
import { LayoutDashboard, History, Users, Settings, LogOut } from "lucide-react";
import { getMe, logout } from "../../lib/adminApi";
import { AdminUIProvider } from "./AdminUIContext";
import { useLanguage } from "../../lib/i18n/LanguageContext";

export default function AdminLayout() {
  const { t } = useLanguage();
  const location = useLocation();
  const [status, setStatus] = useState("checking"); // checking | in | out

  const LINKS = [
    { to: "/admin/dashboard", label: t("admin.nav.dashboard"), icon: LayoutDashboard },
    { to: "/admin/history", label: t("admin.nav.history"), icon: History },
    { to: "/admin/customers", label: t("admin.nav.customers"), icon: Users },
    { to: "/admin/settings", label: t("admin.nav.settings"), icon: Settings },
  ];

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((data) => {
        if (!cancelled) setStatus(data.is_admin ? "in" : "out");
      })
      .catch(() => {
        if (!cancelled) setStatus("out");
      });
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  if (status === "checking") {
    return <div className="min-h-screen bg-canvas px-6 py-24 text-center text-content/75">{t("admin.checkingSession")}</div>;
  }

  if (status === "out") {
    return <Navigate to={`/admin/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  }

  async function handleLogout() {
    await logout();
    setStatus("out");
  }

  return (
    <AdminUIProvider>
    <div className="min-h-screen bg-canvas text-content">
      <header className="border-b border-line px-6 py-4">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4">
          <span className="font-display text-xl">
            {t("admin.brandPrefix")} <span className="text-accent">{t("admin.brandSuffix")}</span>
          </span>
          <nav className="hidden items-center gap-6 md:flex">
            {LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) =>
                  `font-body text-sm font-medium transition-colors ${
                    isActive ? "text-accent" : "text-content/70 hover:text-accent"
                  }`
                }
              >
                {link.label}
              </NavLink>
            ))}
            <button
              type="button"
              onClick={handleLogout}
              className="font-body text-sm font-medium text-content/70 underline transition-colors hover:text-accent"
            >
              {t("admin.logOut")}
            </button>
          </nav>
          <div className="flex items-center gap-3 md:hidden">
            <button
              type="button"
              onClick={handleLogout}
              aria-label={t("admin.logOut")}
              className="text-content/70 transition-colors hover:text-accent"
            >
              <LogOut size={20} />
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-10 pb-28 md:pb-10">
        <Outlet />
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-40 px-4 pb-[calc(env(safe-area-inset-bottom)+1rem)] pt-2 md:hidden">
        <div className="mx-auto flex max-w-sm items-center justify-around rounded-full border border-line bg-surface/95 px-2 py-2.5 shadow-2xl backdrop-blur-md">
          {LINKS.map((link) => {
            const Icon = link.icon;
            return (
              <NavLink
                key={link.to}
                to={link.to}
                className="flex flex-1 flex-col items-center gap-1 py-1"
              >
                {({ isActive }) => (
                  <>
                    <Icon size={22} strokeWidth={isActive ? 2.4 : 1.8} className={isActive ? "text-accent" : "text-content/70"} />
                    <span
                      className={`text-[10px] font-semibold transition-colors ${
                        isActive ? "text-accent" : "text-content/70"
                      }`}
                    >
                      {link.label}
                    </span>
                    <span className={`mt-0.5 h-1 w-1 rounded-full transition-colors ${isActive ? "bg-accent" : "bg-transparent"}`} />
                  </>
                )}
              </NavLink>
            );
          })}
        </div>
      </nav>
    </div>
    </AdminUIProvider>
  );
}
