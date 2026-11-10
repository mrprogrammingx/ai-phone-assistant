import { useState } from "react";
import { motion } from "framer-motion";
import { Expand } from "lucide-react";
import Reveal from "../components/Reveal";
import Lightbox from "../components/Lightbox";
import { GALLERY } from "../lib/content";
import { useLanguage } from "../lib/i18n/LanguageContext";

export default function GalleryGrid({ limit }) {
  const { t } = useLanguage();
  const [activeIndex, setActiveIndex] = useState(null);
  const images = (limit ? GALLERY.slice(0, limit) : GALLERY).map((image) => ({
    ...image,
    alt: t(`gallery.${image.id}`),
  }));

  return (
    <>
      <div className="columns-1 gap-5 sm:columns-2 lg:columns-3 [&>*]:mb-5">
        {images.map((image, i) => (
          <Reveal key={image.id} delay={(i % 3) * 0.08}>
            <motion.button
              type="button"
              onClick={() => setActiveIndex(i)}
              whileHover="hover"
              initial="rest"
              className={`group relative block w-full overflow-hidden rounded-lg bg-surface ${
                image.tall ? "aspect-[3/4]" : "aspect-[4/3]"
              }`}
            >
              <motion.img
                src={image.src}
                alt={image.alt}
                variants={{ rest: { scale: 1 }, hover: { scale: 1.06 } }}
                transition={{ duration: 0.5 }}
                className="h-full w-full object-cover"
                loading="lazy"
              />
              <motion.div
                variants={{ rest: { opacity: 0 }, hover: { opacity: 1 } }}
                className="absolute inset-0 flex items-center justify-center bg-canvas/50"
              >
                <Expand className="text-accent" size={28} />
              </motion.div>
            </motion.button>
          </Reveal>
        ))}
      </div>

      <Lightbox
        images={images}
        index={activeIndex}
        onClose={() => setActiveIndex(null)}
        onNavigate={setActiveIndex}
      />
    </>
  );
}
