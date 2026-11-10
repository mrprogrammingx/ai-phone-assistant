import { Link } from "react-router-dom";
import { MapPin, Mail, Phone } from "lucide-react";
import OpeningHours from "./OpeningHours";
import { SHOP } from "../lib/content";
import { useLanguage } from "../lib/i18n/LanguageContext";

export default function Footer() {
  const { t } = useLanguage();

  return (
    <footer className="border-t border-line bg-surface-2">
      <div className="mx-auto max-w-7xl px-6 py-16 md:px-10">
        <div className="grid grid-cols-1 gap-12 md:grid-cols-4">
          <div>
            <Link to="/" className="font-display text-2xl font-semibold text-content">
              {SHOP.name}
            </Link>
            <p className="mt-4 max-w-xs text-sm leading-relaxed text-muted">{t("footer.tagline")}</p>
          </div>

          <div>
            <h4 className="eyebrow mb-5">{t("footer.hours")}</h4>
            <OpeningHours className="space-y-2 text-sm text-muted" timeClassName="text-content" />
          </div>

          <div>
            <h4 className="eyebrow mb-5">{t("footer.visit")}</h4>
            <ul className="space-y-4 text-sm text-muted">
              <li className="flex items-start gap-3">
                <MapPin size={16} className="mt-0.5 shrink-0 text-accent" />
                <span>{SHOP.address}</span>
              </li>
              {SHOP.phones.map((phone) => (
                <li key={phone} className="flex items-center gap-3">
                  <Phone size={16} className="shrink-0 text-accent" />
                  <a href={`tel:${phone.replace(/[^\d+]/g, "")}`} className="hover:text-accent">
                    {phone}
                  </a>
                </li>
              ))}
              <li className="flex items-center gap-3">
                <Mail size={16} className="shrink-0 text-accent" />
                <a href={`mailto:${SHOP.email}`} className="hover:text-accent">
                  {SHOP.email}
                </a>
              </li>
            </ul>
          </div>

          <div className="flex h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line bg-surface text-sm text-muted md:h-full">
            <MapPin size={20} />
            {t("footer.mapPlaceholder")}
          </div>
        </div>

        <div className="mt-14 flex flex-col items-center justify-between gap-4 border-t border-line pt-8 text-xs text-muted md:flex-row">
          <p>{t("footer.rights", { year: new Date().getFullYear() })}</p>
          <p>{t("footer.tagline2")}</p>
        </div>
      </div>
    </footer>
  );
}
