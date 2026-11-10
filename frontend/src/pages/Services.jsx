import PageHeader from "../components/PageHeader";
import Section from "../components/Section";
import ServicesGrid from "../sections/ServicesGrid";
import { useLanguage } from "../lib/i18n/LanguageContext";

export default function Services() {
  const { t } = useLanguage();
  return (
    <>
      <PageHeader eyebrow={t("services.eyebrow")} title={t("services.title")} subtitle={t("services.subtitle")} />
      <Section>
        <ServicesGrid showCta />
      </Section>
    </>
  );
}
