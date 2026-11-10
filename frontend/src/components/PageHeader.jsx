import Reveal from "./Reveal";

export default function PageHeader({ eyebrow, title, subtitle }) {
  return (
    <div className="border-b border-line bg-surface/40 px-6 pb-16 pt-40 md:px-10 md:pt-48">
      <div className="mx-auto max-w-4xl text-center">
        {eyebrow && (
          <Reveal>
            <p className="eyebrow mb-4">{eyebrow}</p>
          </Reveal>
        )}
        <Reveal delay={0.05}>
          <h1 className="section-heading">{title}</h1>
        </Reveal>
        {subtitle && (
          <Reveal delay={0.1}>
            <p className="mt-5 text-muted">{subtitle}</p>
          </Reveal>
        )}
      </div>
    </div>
  );
}
