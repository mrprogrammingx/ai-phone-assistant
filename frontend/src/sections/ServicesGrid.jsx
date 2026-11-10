import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { Scissors } from "lucide-react";
import Reveal from "../components/Reveal";
import { SERVICES } from "../lib/content";
import { useLanguage } from "../lib/i18n/LanguageContext";

export default function ServicesGrid({ limit, showCta = false }) {
  const { t } = useLanguage();
  const services = limit ? SERVICES.slice(0, limit) : SERVICES;

  return (
    <div>
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {services.map((service, i) => (
          <Reveal key={service.id} delay={i * 0.08}>
            <motion.div
              whileHover={{ y: -6 }}
              transition={{ duration: 0.3 }}
              className={`group relative flex h-full flex-col justify-between overflow-hidden rounded-lg border p-8 ${
                service.featured
                  ? "border-accent/40 bg-gradient-to-br from-surface-2 to-surface"
                  : "border-line bg-surface"
              }`}
            >
              {service.featured && (
                <span className="absolute right-6 top-6 rounded-full bg-accent/15 px-3 py-1 text-[10px] font-semibold text-accent">
                  {t("services.popular")}
                </span>
              )}
              <div>
                <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-full bg-accent/10 text-accent transition-colors group-hover:bg-accent group-hover:text-canvas">
                  <Scissors size={20} />
                </div>
                <h3 className="font-display text-2xl text-content">{t(`service.${service.id}.name`)}</h3>
                <p className="mt-3 text-sm leading-relaxed text-muted">{t(`service.${service.id}.description`)}</p>
              </div>
            </motion.div>
          </Reveal>
        ))}
      </div>

      {showCta && (
        <Reveal delay={0.2} className="mt-14 text-center">
          <Link to="/booking" className="btn-primary">
            {t("services.bookYourChair")}
          </Link>
        </Reveal>
      )}
    </div>
  );
}
