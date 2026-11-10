// All user-facing text lives here, as flat keys. English is the only language.

export const DEFAULT_LANGUAGE = "en";

const en = {
  // Nav / common
  "nav.home": "Home",
  "nav.contact": "Contact",
  "nav.staffLogin": "Staff login",
  "nav.bookNow": "Book now",
  "nav.contactUs": "Contact us",

  // Days of week
  "day.mon": "Monday",
  "day.tue": "Tuesday",
  "day.wed": "Wednesday",
  "day.thu": "Thursday",
  "day.fri": "Friday",
  "day.sat": "Saturday",
  "day.sun": "Sunday",
  "day.closed": "Closed",
  "hours.unavailable": "Opening hours are unavailable right now.",

  // Footer
  "footer.tagline": "Straightforward haircuts and beard work, with your chair ready at the time we agreed.",
  "footer.hours": "Opening hours",
  "footer.visit": "Find us",
  "footer.rights": "© {year} Demo Barbershop",
  "footer.mapPlaceholder": "Map placeholder",
  "footer.tagline2": "A fictional shop used to demonstrate online and phone booking.",

  // Hero
  "hero.eyebrow": "Your neighbourhood barber in Zürich",
  "hero.headline": "A better haircut, at a time that suits you.",
  "hero.imageAlt": "Black barber chair on a checkerboard floor",
  "hero.subtitle":
    "Pick a time here, or ring the shop and our phone assistant will find you a slot. Either way it goes in the same diary.",

  // Home
  "home.eyebrow": "Online booking",
  "home.title": "Pick a time that works",

  // Services page
  "services.eyebrow": "What we offer",
  "services.title": "Services",
  "services.subtitle": "Six appointments, from a quick tidy-up to a full cut and beard.",
  "services.popular": "Customer favourite",
  "services.bookYourChair": "Book an appointment",

  "service.signature-fade.name": "Signature Fade",
  "service.signature-fade.description": "A clean, gradual fade at the length you choose, with tidy edges around the ears and neck.",
  "service.classic-cut.name": "Classic Cut",
  "service.classic-cut.description": "A timeless scissor haircut, shaped around how you wear your hair day to day.",
  "service.beard-sculpt.name": "Beard Sculpt",
  "service.beard-sculpt.description": "Trimmed and shaped so your beard sits neatly along the jaw and cheek.",
  "service.hot-towel-shave.name": "Hot Towel Shave",
  "service.hot-towel-shave.description": "A slow, close shave, with warm towels to soften the skin first.",
  "service.cut-beard-combo.name": "Cut + Beard Combo",
  "service.cut-beard-combo.description": "A haircut and a beard trim in one longer visit.",
  "service.junior-cut.name": "Junior Cut",
  "service.junior-cut.description": "A relaxed haircut for children aged 12 and under.",
  "duration.min": "{n} min",

  // Gallery
  "gallery.eyebrow": "Around the shop",
  "gallery.title": "Photos",
  "gallery.subtitle": "Select a photo to see it larger.",
  "gallery.g1": "Chrome and leather barber chair in a dark shop",
  "gallery.g2": "Barber shaping a fade with clippers",
  "gallery.g3": "Barber cutting a client's hair with scissors against a brick wall",
  "gallery.g4": "Barber cutting a client's hair with scissors and a comb",
  "gallery.g5": "Brushes, scissors and clips arranged on a tool board",
  "gallery.g6": "Black and white view of a row of barber chairs",
  "gallery.g7": "Barber in black gloves cutting hair",
  "gallery.g8": "Barber chairs in a shop lit with warm golden light",
  "gallery.g9": "Barber trimming hair with thinning shears and a comb",
  "lightbox.close": "Close",
  "lightbox.previous": "Previous image",
  "lightbox.next": "Next image",

  // Booking page
  "booking.eyebrow": "Appointments",
  "booking.title": "Book online",
  "booking.subtitle": "Choose a day and a free time, add your details, and you are done. No account needed.",
  "booking.alreadyBookedEyebrow": "Booked already?",
  "booking.alreadyBookedTitle": "Check your appointment",

  // Booking form
  "bookingForm.chooseDate": "Choose a date",
  "bookingForm.availableTimes": "Available times",
  "bookingForm.loadingTimes": "Loading times…",
  "bookingForm.closedOnDate": "The shop is closed on this date. Try another day.",
  "bookingForm.fullName": "Full name",
  "bookingForm.fullNamePlaceholder": "Max Muster",
  "bookingForm.phoneNumber": "Phone number",
  "bookingForm.phonePlaceholder": "+41 79 000 00 00",
  "bookingForm.noteLabel": "Note (optional)",
  "bookingForm.notePlaceholder": "Anything we should know, such as short on the sides",
  "bookingForm.booking": "Booking…",
  "bookingForm.confirmAppointment": "Confirm booking",
  "bookingForm.youAreBooked": "See you soon, {name}",
  "bookingForm.queuePosition": "Queue position #{n}",
  "bookingForm.bookAnother": "Book another appointment",

  // My bookings lookup
  "myBookings.placeholder": "Enter the phone number you booked with",
  "myBookings.findButton": "Find my booking",
  "myBookings.noResults": "No bookings found for that number.",
  "status.waiting": "Waiting",
  "status.next": "Next",
  "status.in_progress": "In progress",
  "status.done": "Done",
  "status.no_show": "No Show",

  // Contact page
  "contact.eyebrow": "Say hello",
  "contact.title": "Contact and location",
  "contact.subtitle": "Call, send an email, or drop in during opening hours.",
  "contact.address": "Address",
  "contact.phone": "Phone",
  "contact.email": "Email",

  // Admin — shared
  "admin.checkingSession": "Checking session…",
  "admin.brandPrefix": "Demo Barbershop",
  "admin.brandSuffix": "Staff",
  "admin.logOut": "Log Out",
  "admin.nav.dashboard": "Dashboard",
  "admin.nav.history": "History",
  "admin.nav.customers": "Customers",
  "admin.nav.settings": "Settings",
  "admin.field.name": "Name",
  "admin.field.phone": "Phone",
  "admin.field.note": "Note",
  "admin.field.status": "Status",
  "admin.field.checkedIn": "Checked In",
  "admin.field.date": "Date:",
  "admin.cancel": "Cancel",
  "admin.confirm": "Confirm",
  "adminStatus.waiting": "Waiting",
  "adminStatus.next": "Processing",
  "adminStatus.in_progress": "Processing",
  "adminStatus.done": "Done",
  "adminStatus.no_show": "No Show",
  "adminStatus.cancelled": "Cancelled",

  // Admin login
  "admin.login.titlePrefix": "Staff",
  "admin.login.titleSuffix": "Login",
  "admin.login.username": "Username",
  "admin.login.password": "Password",
  "admin.login.loggingIn": "Logging in…",
  "admin.login.logIn": "Log In",
  "admin.login.invalidCredentials": "Invalid username or password.",
  "admin.login.viewAsCustomer": "View site as a customer",

  // Admin dashboard
  "admin.dashboard.title": "Staff Dashboard",
  "admin.dashboard.noEntries": "No queue entries for this date.",
  "admin.dashboard.addNote": "Add a note",
  "admin.table.number": "#",
  "admin.table.time": "Time",

  // Admin history
  "admin.history.title": "Queue History",
  "admin.history.datesWithHistory": "Dates with history: {dates}",
  "admin.history.noHistoryYet": "No past queue history yet.",

  // Admin customers
  "admin.customers.title": "Customers",
  "admin.customers.showAll": "Show All",
  "admin.customers.showingAll": "Showing all customers. Pick a date to see only who's booked that day.",
  "admin.customers.showingDate": "Showing customers booked on {date}.",
  "admin.customers.noneFound": "No customers found.",
  "admin.table.visits": "Visits",
  "admin.table.signedUp": "Signed Up",

  // Admin settings
  "admin.settings.title": "Settings",
  "admin.settings.bookingToday": "Booking Today",
  "admin.settings.bookingTodayDesc":
    "Turn off new bookings for today only, without changing the regular weekly schedule. Other dates stay bookable.",
  "admin.settings.bookingOpenToday": "Booking is open for today",
  "admin.settings.bookingClosedToday": "Booking is closed for today",
  "admin.settings.toggleAria": "Toggle booking open or closed for today",
  "admin.settings.daysOpen": "Days Open",
  "admin.settings.saveDaysOpen": "Save Days Open",
  "admin.settings.daysOpenSaved": "Days open saved.",
  "admin.settings.appointmentSlots": "Appointment Slots",
  "admin.settings.open": "Open",
  "admin.settings.close": "Close",
  "admin.settings.slotLength": "Slot length (minutes)",
  "admin.settings.capacityPerSlot": "Capacity per slot",
  "admin.settings.saveSlotSettings": "Save Slot Settings",
  "admin.settings.slotSettingsSaved": "Slot settings saved.",
  "admin.settings.notificationEmails": "Notification Emails",
  "admin.settings.notificationEmailsDesc":
    "Email addresses notified whenever a customer checks in. One per line or comma-separated.",
  "admin.settings.saveNotificationEmails": "Save Notification Emails",
  "admin.settings.emailsSaved": "Notification emails saved.",
  "admin.settings.emailsError": "Error: {message}",
  "admin.settings.manageTimeSlots": "Manage Time Slots",
  "admin.settings.manageTimeSlotsDesc": "Click a normal time to block/unblock it. Extra times (blue) can be removed by clicking them.",
  "admin.settings.slotBlockTitle": "Click to block/unblock",
  "admin.settings.slotExtraTitle": "Extra time — click to remove",
  "admin.settings.addExtraTime": "Add Extra Time for This Date",
  "admin.settings.loading": "Loading…",

  "picker.selectDate": "Select a date",
  "picker.chooseDate": "Choose date",
  "picker.close": "Close",
  "picker.hour": "Hour",
  "picker.minute": "Minute",
  "picker.previousMonth": "Previous month",
  "picker.nextMonth": "Next month",
};

export const TRANSLATIONS = { en };
