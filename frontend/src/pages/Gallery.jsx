import PageHeader from "../components/PageHeader";
import Section from "../components/Section";
import GalleryGrid from "../sections/GalleryGrid";
import { useLanguage } from "../lib/i18n/LanguageContext";

export default function Gallery() {
  const { t } = useLanguage();
  return (
    <>
      <PageHeader eyebrow={t("gallery.eyebrow")} title={t("gallery.title")} subtitle={t("gallery.subtitle")} />
      <Section>
        <GalleryGrid />
      </Section>
    </>
  );
}
