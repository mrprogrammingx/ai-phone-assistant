# Demo Barbershop: frontend

Website and staff UI for a fictional barbershop, built with React, Tailwind CSS and Framer
Motion. It is a separate app from the FastAPI backend in `../app` and talks to that API over
HTTP for availability, bookings and opening hours.

All shop details in this app are fictional.

## Stack

- **React 19 + Vite**
- **Tailwind CSS 4** (config lives in `src/index.css` via `@theme`)
- **Framer Motion** for reveals and page transitions
- **React Router** for Home, Services, Gallery, Booking, Contact and the staff pages
- **Lenis** for smooth scrolling
- **lucide-react** for icons

## Setup

```bash
cd frontend
npm ci
npm run dev
```

Runs at http://localhost:5173. In dev, Vite proxies `/queue`, `/admin` and `/api` requests to
`http://localhost:8002` (see `vite.config.js`), so start the backend too:

```bash
# from the repo root, in another terminal
venv/bin/uvicorn app.main:app --reload --port 8002
```

The booking form calls `/queue/available-times` and `/queue/checkin`, the lookup calls
`/queue/my-bookings`, and the footer and Contact page read opening hours from
`/admin/shop-status`. Bookings made here show up in the staff dashboard at `/admin/dashboard`.

## Environment variables

`VITE_API_BASE_URL`: leave unset in dev (uses the Vite proxy). Set it to the deployed API origin
in production.

## Content

Shop details and gallery images live in `src/lib/content.js`. All text lives in
`src/lib/i18n/translations.js`. Services come from `../shared/services.json`, which the phone
assistant reads too. Photos are hotlinked stock images from Unsplash.

## Build

```bash
npm run build   # outputs to dist/
npm run preview # serve the production build locally
```
