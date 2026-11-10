import { createContext, useContext } from "react";
import { DEFAULT_LANGUAGE, TRANSLATIONS } from "./translations";

const LanguageContext = createContext(null);

function interpolate(template, vars) {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, key) => (key in vars ? String(vars[key]) : match));
}

function t(key, vars) {
  return interpolate(TRANSLATIONS[DEFAULT_LANGUAGE][key] ?? key, vars);
}

const VALUE = { t };

export function LanguageProvider({ children }) {
  return <LanguageContext.Provider value={VALUE}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error("useLanguage must be used within LanguageProvider");
  return ctx;
}
