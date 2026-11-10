import PageHeader from "../components/PageHeader";
import Section from "../components/Section";
import BookingForm from "../sections/BookingForm";
import MyBookingsLookup from "../sections/MyBookingsLookup";
import { useLanguage } from "../lib/i18n/LanguageContext";

export default function Booking() {
  const { t } = useLanguage();
  return (
    <>
      <PageHeader eyebrow={t("booking.eyebrow")} title={t("booking.title")} subtitle={t("booking.subtitle")} />
      <Section>
        <BookingForm />
      </Section>
      <Section eyebrow={t("booking.alreadyBookedEyebrow")} title={t("booking.alreadyBookedTitle")} className="bg-surface/40">
        <MyBookingsLookup />
      </Section>
    </>
  );
}
