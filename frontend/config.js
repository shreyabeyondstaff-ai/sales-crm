// ============ AUTH SWITCH (frontend half) ============
// false = test mode: no login, a role switcher appears in the top bar (backend must have AUTH_DISABLED=true)
// true  = real Firebase login. Fill FIREBASE below. (backend must have AUTH_DISABLED=false)
window.APP_CONFIG = {
  AUTH_ENABLED: true,
  API_BASE: "/api/v1",
  FIREBASE: { apiKey: "AIzaSyC2XnnmxBndBUpL2WgahJaB8L3VF-8pX-s", authDomain: "outreach-crm-a6269.firebaseapp.com", projectId:  "outreach-crm-a6269", appId: "1:324232068629:web:73ed39f9a0e6504897181c" }
};
