# Create a Google OAuth client

Use this route only when no usable Google Desktop OAuth client JSON is
available. Recommend setting up Drive, Calendar, and Gmail, but ask which
subset the user wants before configuring APIs and scopes.

Guide the user through the current Google Auth Platform UI:

1. Select an existing Google Cloud project or create one.
2. Configure the app audience as External. If its publishing status is
   Testing, add the exact Google email address of every intended user under
   Test users. State the limits in these exact words, which the router also
   uses when explaining why this setup cannot be automated; keep both copies
   identical:

   > Google's OAuth user cap allows at most 100 manually
   > listed test users, and their refresh tokens expire after seven days, so those
   > users must authorize again.

   Publishing removes the manual test-user gate but not the cap for an
   unverified app; verification is the scaling path beyond it.
3. Configure Branding with an accurate app name and support email.
4. Enable the Google Drive API when Drive is selected and the Calendar API when
   Calendar is selected. The Gmail integration uses IMAP/SMTP
   XOAUTH2; it does not require the Gmail REST API.
5. Under Data Access, register only the selected services' current scopes:
   - Drive: `https://www.googleapis.com/auth/drive`
   - Calendar: `https://www.googleapis.com/auth/calendar`
   - Gmail IMAP/SMTP: `https://mail.google.com/`
6. Under Clients, create an OAuth client of application type Desktop app and
   download its JSON file.

Explain that the OAuth client identifies the app but grants no account access
until the user completes browser authorization. When the OAuth app is in
Testing, its test-user allowlist controls who may authorize. It does not distribute
the client JSON. The downloaded JSON remains local to the user who created the
client. A Google Workspace administrator can still block authorization even
for a listed test user.

Keep the downloaded JSON private. Never commit it, paste its contents into chat,
or put it in an issue. Once the user gives the local file path, continue in
`connect-google.interface.connect-services` without restarting the workflow.

@../personal-preferences/create-client.md
