import Reveal from "./Reveal";

export default function Section({ id, eyebrow, title, subtitle, children, className = "", contained = true }) {
  return (
    <section id={id} className={`py-24 md:py-32 ${className}`}>
      <div className={contained ? "mx-auto max-w-7xl px-6 md:px-10" : ""}>
        {(eyebrow || title) && (
          <div className="mx-auto mb-14 max-w-2xl text-center md:mb-20">
            {eyebrow && (
              <Reveal>
                <p className="eyebrow mb-4">{eyebrow}</p>
              </Reveal>
            )}
            {title && (
              <Reveal delay={0.05}>
                <h2 className="section-heading">{title}</h2>
              </Reveal>
            )}
            {subtitle && (
              <Reveal delay={0.1}>
                <p className="mt-5 text-muted">{subtitle}</p>
              </Reveal>
            )}
          </div>
        )}
        {children}
      </div>
    </section>
  );
}
