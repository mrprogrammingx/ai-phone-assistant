import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { HERO_IMAGE } from "../lib/content";
import { useLanguage } from "../lib/i18n/LanguageContext";

const rise = (delay) => ({
  initial: { opacity: 0, y: 16 },
  animate: { opacity: 1, y: 0 },
  transition: { delay, duration: 0.6 },
});

export default function Hero() {
  const { t } = useLanguage();

  return (
    <section className="px-6 pb-16 pt-32 md:px-10 md:pb-24 md:pt-40">
      <div className="mx-auto grid max-w-7xl items-center gap-12 md:grid-cols-[1.05fr_0.95fr] lg:gap-20">
        <div>
          <motion.p {...rise(0.1)} className="eyebrow mb-5">
            {t("hero.eyebrow")}
          </motion.p>

          <motion.h1
            {...rise(0.2)}
            className="font-display text-5xl font-semibold leading-[1.08] text-content md:text-6xl lg:text-7xl"
          >
            {t("hero.headline")}
          </motion.h1>

          <motion.p {...rise(0.35)} className="mt-7 max-w-lg text-base leading-relaxed text-muted md:text-lg">
            {t("hero.subtitle")}
          </motion.p>

          <motion.div {...rise(0.5)} className="mt-9 flex flex-wrap items-center gap-4">
            <Link to="/booking" className="btn-primary">
              {t("nav.bookNow")}
            </Link>
            <Link to="/contact" className="btn-outline">
              {t("nav.contactUs")}
            </Link>
          </motion.div>
        </div>

        <motion.div {...rise(0.3)} className="overflow-hidden rounded-t-[10rem] rounded-b-lg shadow-card">
          <img src={HERO_IMAGE} alt={t("hero.imageAlt")} className="h-[420px] w-full object-cover md:h-[600px]" />
        </motion.div>
      </div>
    </section>
  );
}
