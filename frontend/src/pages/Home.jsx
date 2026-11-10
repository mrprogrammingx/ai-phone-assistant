import Hero from "../sections/Hero";
import Section from "../components/Section";
import BookingForm from "../sections/BookingForm";
import { useLanguage } from "../lib/i18n/LanguageContext";

export default function Home() {
  const { t } = useLanguage();
  return (
    <>
      <Hero />

      <Section eyebrow={t("home.eyebrow")} title={t("home.title")}>
        <BookingForm />
      </Section>
    </>
  );
}
