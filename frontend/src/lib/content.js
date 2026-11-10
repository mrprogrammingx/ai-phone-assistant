// Central place for shop details and imagery. Everything here is fictional demo
// data. Every image is referenced from here, nowhere else.

import serviceCatalog from "../../../shared/services.json";

const unsplash = (id, params = "w=1600&q=80&auto=format&fit=crop") =>
  `https://images.unsplash.com/photo-${id}?${params}`;

export const SHOP = {
  name: "Demo Barbershop",
  phones: ["+41 44 000 00 00", "+41 79 000 00 00"],
  email: "info@example.com",
  address: "Musterstrasse 1, 8000 Zürich",
};

export const HERO_IMAGE = unsplash("1611313151697-d626e818dddf", "w=2400&q=80&auto=format&fit=crop");

// The backend reads the same file, so the website and the phone assistant agree.
export const SERVICES = serviceCatalog.services;

export const GALLERY = [
  { id: "g1", src: unsplash("1621645582931-d1d3e6564943"), alt: "Chrome and leather barber chair in a dark shop", tall: true },
  { id: "g2", src: unsplash("1593702275687-f8b402bf1fb5"), alt: "Barber shaping a fade with clippers" },
  { id: "g3", src: unsplash("1768363530219-2db2db454b46"), alt: "Barber cutting a client's hair with scissors against a brick wall" },
  { id: "g4", src: unsplash("1647140655214-e4a2d914971f"), alt: "Barber cutting a client's hair with scissors and a comb", tall: true },
  { id: "g5", src: unsplash("1656921350183-7935040cf7fb"), alt: "Brushes, scissors and clips arranged on a tool board" },
  { id: "g6", src: unsplash("1781768526571-ef9c333279c7"), alt: "Black and white view of a row of barber chairs" },
  { id: "g7", src: unsplash("1783908688260-0c47404926f3"), alt: "Barber in black gloves cutting hair" },
  { id: "g8", src: unsplash("1773904215697-e6c21fc27ac2"), alt: "Barber chairs in a shop lit with warm golden light", tall: true },
  { id: "g9", src: unsplash("1657105052497-f996284ffff8"), alt: "Barber trimming hair with thinning shears and a comb" },
];
