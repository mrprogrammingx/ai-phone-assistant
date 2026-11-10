import { useEffect, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { Menu, Phone, X } from "lucide-react";
import { SHOP } from "../lib/content";
import { useLanguage } from "../lib/i18n/LanguageContext";

const ADMIN_LOGIN_URL = "/admin/login";

export default function Navbar() {
  const { t } = useLanguage();
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  const LINKS = [
    { to: "/", label: t("nav.home") },
    { to: "/contact", label: t("nav.contact") },
  ];

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  return (
    <>
      <motion.header
        initial={false}
        animate={{
          paddingTop: scrolled ? "0.75rem" : "1.5rem",
          paddingBottom: scrolled ? "0.75rem" : "1.5rem",
        }}
        transition={{ duration: 0.35, ease: "easeOut" }}
        className={`fixed inset-x-0 top-0 z-50 px-6 transition-colors duration-300 md:px-10 ${
          scrolled ? "border-b border-line bg-canvas/90 backdrop-blur-md" : "bg-transparent"
        }`}
      >
        <nav className="mx-auto flex max-w-7xl items-center justify-between">
          <Link
            to="/"
            className="font-display text-xl font-semibold text-content md:text-2xl"
            onClick={() => setOpen(false)}
          >
            {SHOP.name}
          </Link>

          <ul className="hidden items-center gap-9 md:flex">
            {LINKS.map((link) => (
              <li key={link.to}>
                <NavLink
                  to={link.to}
                  className={({ isActive }) =>
                    `font-body text-sm font-medium transition-colors ${
                      isActive ? "text-accent" : "text-content/80 hover:text-accent"
                    }`
                  }
                >
                  {link.label}
                </NavLink>
              </li>
            ))}
          </ul>

          <div className="hidden items-center gap-6 md:flex">
            <a
              href={ADMIN_LOGIN_URL}
              className="font-body text-sm font-medium text-content/75 transition-colors hover:text-accent"
            >
              {t("nav.staffLogin")}
            </a>
            <Link to="/booking" className="btn-primary !px-5 !py-2.5">
              {t("nav.bookNow")}
            </Link>
          </div>

          <button
            type="button"
            aria-label="Toggle menu"
            onClick={() => setOpen((v) => !v)}
            className="text-content md:hidden"
          >
            {open ? <X size={26} /> : <Menu size={26} />}
          </button>
        </nav>
      </motion.header>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
            className="fixed inset-0 z-40 flex flex-col justify-between bg-canvas px-8 pb-10 pt-28 md:hidden"
          >
            <ul className="flex flex-col gap-6">
              {LINKS.map((link, i) => (
                <motion.li
                  key={link.to}
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.08 * i + 0.1 }}
                >
                  <NavLink
                    to={link.to}
                    onClick={() => setOpen(false)}
                    className={({ isActive }) =>
                      `font-display text-4xl ${isActive ? "text-accent" : "text-content"}`
                    }
                  >
                    {link.label}
                  </NavLink>
                </motion.li>
              ))}
            </ul>

            <div className="flex flex-col gap-6">
              {SHOP.phones.map((phone) => (
                <a
                  key={phone}
                  href={`tel:${phone.replace(/[^\d+]/g, "")}`}
                  className="flex items-center gap-3 text-content/80"
                >
                  <Phone size={18} className="text-accent" /> {phone}
                </a>
              ))}
              <Link to="/booking" onClick={() => setOpen(false)} className="btn-primary w-full">
                {t("nav.bookNow")}
              </Link>
              <a href={ADMIN_LOGIN_URL} className="text-sm text-content/70">
                {t("nav.staffLogin")}
              </a>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
