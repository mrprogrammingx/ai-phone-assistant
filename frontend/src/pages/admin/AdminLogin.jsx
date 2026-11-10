import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { login } from "../../lib/adminApi";
import { inputClass, buttonClass } from "../../components/admin/ui";
import { useLanguage } from "../../lib/i18n/LanguageContext";

export default function AdminLogin() {
  const { t } = useLanguage();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const next = searchParams.get("next") || "/admin/dashboard";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username, password);
      navigate(next, { replace: true });
    } catch (err) {
      setError(err.message || t("admin.login.invalidCredentials"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-6 py-24 text-content">
      <form onSubmit={handleSubmit} className="w-full max-w-sm rounded-lg border border-line bg-surface shadow-card p-8">
        <div className="mb-6 flex items-center justify-between gap-3">
          <h1 className="font-display text-2xl">
            {t("admin.login.titlePrefix")} <span className="text-accent">{t("admin.login.titleSuffix")}</span>
          </h1>
        </div>
        {error && <p className="mb-4 rounded-lg bg-red-100 px-3 py-2 text-sm text-red-800">{error}</p>}
        <div className="flex flex-col gap-4">
          <input
            type="text"
            placeholder={t("admin.login.username")}
            required
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className={inputClass}
          />
          <input
            type="password"
            placeholder={t("admin.login.password")}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
          />
          <button type="submit" disabled={submitting} className={buttonClass}>
            {submitting ? t("admin.login.loggingIn") : t("admin.login.logIn")}
          </button>
        </div>

        <Link
          to="/"
          className="mt-6 flex items-center justify-center gap-2 text-sm text-content/70 transition-colors hover:text-accent"
        >
          <ArrowLeft size={14} />
          {t("admin.login.viewAsCustomer")}
        </Link>
      </form>
    </div>
  );
}
