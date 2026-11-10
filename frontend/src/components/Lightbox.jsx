import { AnimatePresence, motion } from "framer-motion";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useEffect, useCallback } from "react";
import { useLanguage } from "../lib/i18n/LanguageContext";

export default function Lightbox({ images, index, onClose, onNavigate }) {
  const { t } = useLanguage();
  const image = index !== null ? images[index] : null;

  const handleKey = useCallback(
    (e) => {
      if (index === null) return;
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") onNavigate((index + 1) % images.length);
      if (e.key === "ArrowLeft") onNavigate((index - 1 + images.length) % images.length);
    },
    [index, images.length, onClose, onNavigate]
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [handleKey]);

  // Locks background scroll so mobile Safari's viewport doesn't shift under
  // a fixed overlay and throw off tap targets (see DatePickerField).
  useEffect(() => {
    if (index === null) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [index]);

  return (
    <AnimatePresence>
      {image && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-canvas/95"
        >
          <button
            aria-label={t("lightbox.close")}
            onClick={onClose}
            className="absolute right-6 top-6 text-content/80 transition-colors hover:text-accent"
          >
            <X size={30} />
          </button>

          <button
            aria-label={t("lightbox.previous")}
            onClick={(e) => {
              e.stopPropagation();
              onNavigate((index - 1 + images.length) % images.length);
            }}
            className="absolute left-4 text-content/70 transition-colors hover:text-accent md:left-8"
          >
            <ChevronLeft size={36} />
          </button>

          <motion.img
            key={image.id}
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96 }}
            transition={{ duration: 0.25 }}
            onClick={(e) => e.stopPropagation()}
            src={image.src}
            alt={image.alt}
            className="max-h-[85vh] max-w-[88vw] rounded-lg object-contain shadow-2xl"
          />

          <button
            aria-label={t("lightbox.next")}
            onClick={(e) => {
              e.stopPropagation();
              onNavigate((index + 1) % images.length);
            }}
            className="absolute right-4 text-content/70 transition-colors hover:text-accent md:right-8"
          >
            <ChevronRight size={36} />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
