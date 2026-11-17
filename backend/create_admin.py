"""Create (or repair) the first admin: a Firebase Auth user with a VERIFIED email + an active admin CRM profile.

Run from the backend folder:   .venv\\Scripts\\python.exe create_admin.py you@example.com
Asks for the password (not echoed). Needs serviceAccount.json (see GOOGLE_APPLICATION_CREDENTIALS in .env).
Safe to re-run: an existing user keeps its account, gets the new password, and is marked verified.
"""
import os, sys, getpass
os.environ.setdefault("USE_FIRESTORE", "true")
import firebase
from firebase_admin import auth

def main():
    if len(sys.argv) != 2 or "@" not in sys.argv[1]: sys.exit("Usage: python create_admin.py you@example.com")
    email = sys.argv[1].strip().lower()
    pw = getpass.getpass("Password (min 6 chars): ")
    if len(pw) < 6 or pw != getpass.getpass("Repeat password: "): sys.exit("Passwords empty/short or do not match.")
    firebase._init_admin()
    try:
        u = auth.get_user_by_email(email); u = auth.update_user(u.uid, password=pw, email_verified=True, disabled=False); print("Existing Firebase user updated.")
    except auth.UserNotFoundError:
        u = auth.create_user(email=email, password=pw, email_verified=True); print("Firebase user created.")
    name = u.display_name or email.split("@")[0]
    prev = firebase.db.get("users", u.uid) or {}; prev.pop("pending", None)
    firebase.db.put("users", u.uid, {**prev, "name": prev.get("name") or name, "email": email, "role": "admin", "active": True, "created_at": prev.get("created_at") or firebase.now()})
    print(f"Admin profile ready for {email}. Sign in at the CRM login page.")

if __name__ == "__main__": main()
