# Google Calendar Agent setup

The Calendar Agent uses Google's official Calendar API and a local OAuth
Desktop client. Do not use a service-account key for a personal calendar.

## 1. Create the OAuth client

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a project for Elaina.
3. Open **APIs & Services → Library**.
4. Enable **Google Calendar API**.
5. Configure the OAuth consent screen.
6. While the app is in testing, add your own Google account as a test user.
7. Open **APIs & Services → Credentials**.
8. Choose **Create credentials → OAuth client ID**.
9. Select **Desktop app**.
10. Download the JSON credential file.

Store that JSON outside the Elaina project.

## 2. Configure `.env`

Copy `.env.example` to `.env`, then set:

```dotenv
GOOGLE_CALENDAR_CREDENTIALS=C:/Users/YourName/Secrets/elaina-calendar.json
```

Use forward slashes or a normal Windows absolute path. Do not commit `.env`,
the credential JSON, or anything under `runtime/secrets/`.

## 3. Create the agent

Run:

```powershell
python main.py
```

Say:

> Create an agent that can add events to my Google Calendar.

Example answer to Elaina's requirements question:

> Use Asia/Seoul, my primary calendar, a 60-minute default, and ask me before every change.

Review and approve the agent installation in Electron.

## 4. Create the first event

Say:

> Add a project meeting tomorrow at 7 PM for one hour.

Review the exact event and approve it in Electron. On the first approved event,
your browser opens Google's OAuth page. Sign in to the intended account and
grant Calendar event access.

Elaina stores the resulting authorization token locally at:

```text
runtime/secrets/google_calendar_token.json
```

Future approved events reuse that token. If authorization is revoked or the
token becomes invalid, remove that token file and approve another event to
authenticate again.

## Supported now

- Ask for missing event title, date, and time
- Resolve relative dates using the configured time zone
- Use a configured default duration
- Preview the exact event
- Create an event after approval

Not implemented yet:

- Update an existing event
- Delete an event
- Invite attendees
- University course registration
- Automatic recurring-event interpretation
