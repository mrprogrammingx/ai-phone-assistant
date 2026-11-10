import { Mail, MapPin, Phone } from "lucide-react";
import { Link } from "react-router-dom";
import PageHeader from "../components/PageHeader";
import Section from "../components/Section";
import Reveal from "../components/Reveal";
import OpeningHours from "../components/OpeningHours";
import { SHOP } from "../lib/content";
import { useLanguage } from "../lib/i18n/LanguageContext";

export default function Contact() {
  const { t } = useLanguage();
  return (
    <>
      <PageHeader eyebrow={t("contact.eyebrow")} title={t("contact.title")} subtitle={t("contact.subtitle")} />

      <Section>
        <div className="mx-auto max-w-2xl">
          <Reveal>
            <div className="flex h-full flex-col justify-between rounded-lg border border-line bg-surface p-8 shadow-card md:p-10">
              <div className="space-y-7">
                <div className="flex items-start gap-4">
                  <MapPin size={20} className="mt-1 shrink-0 text-accent" />
                  <div>
                    <p className="eyebrow mb-1">{t("contact.address")}</p>
                    <p className="text-content">{SHOP.address}</p>
                  </div>
                </div>
                <div className="flex items-start gap-4">
                  <Phone size={20} className="mt-1 shrink-0 text-accent" />
                  <div>
                    <p className="eyebrow mb-1">{t("contact.phone")}</p>
                    <div className="flex flex-col gap-1">
                      {SHOP.phones.map((phone) => (
                        <a
                          key={phone}
                          href={`tel:${phone.replace(/[^\d+]/g, "")}`}
                          className="text-content hover:text-accent"
                        >
                          {phone}
                        </a>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="flex items-start gap-4">
                  <Mail size={20} className="mt-1 shrink-0 text-accent" />
                  <div>
                    <p className="eyebrow mb-1">{t("contact.email")}</p>
                    <a href={`mailto:${SHOP.email}`} className="text-content hover:text-accent">
                      {SHOP.email}
                    </a>
                  </div>
                </div>
              </div>

              <div className="mt-10 border-t border-line pt-8">
                <p className="eyebrow mb-4">{t("footer.hours")}</p>
                <OpeningHours className="space-y-2 text-sm text-muted" timeClassName="text-content" />
              </div>

              <Link to="/booking" className="btn-primary mt-10 w-full">
                {t("services.bookYourChair")}
              </Link>
            </div>
          </Reveal>
        </div>
      </Section>
    </>
  );
}
