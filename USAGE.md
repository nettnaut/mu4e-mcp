# Using mu4e-mcp — how to ask Claude to search & read your email

This is a guide for *humans*. Once mu4e-mcp is installed (see the
[README](README.md)), you don't call any tools or write `mu` queries — you just
describe what you're looking for in plain language, and Claude translates it
into a `mu` search, runs it against your **locally indexed** mail, and shows you
the results. Nothing leaves your machine; the server only *reads* mail.

## The one thing to know

Ask the way you'd ask an assistant who can see your mailbox:

> "Find unread emails from my boss this week, and show me the latest one."

Claude turns that into a `mu` query, lists the matches, then opens the message
you mean.

## What you can ask for, with examples

### Find emails
- "Search for unread emails in my work inbox."
- "Find emails from alice@example.com in the last 7 days."
- "Any emails about the invoice from last month?"
- "Show me mail with 'contract' in the subject from the personal folder."
- "What's unread and not trashed across everything?"

Claude maps these to `mu` queries (sender, subject, folder/`maildir`, date
ranges, flags). You don't need the syntax — but you can give it if you like:
"run the query `flag:unread maildir:/work/INBOX`".

### Read an email
After a search, refer to a result naturally:
- "Open the second one."
- "Show me the body of the email from Alice."
- "What did the vendor actually ask for in that thread?"

Claude fetches the full headers and body for that message.

### Work across both
- "Find the latest email from the client and summarise what they need."
- "Search for unread mail from this week and tell me which ones need a reply."
- "Find the booking confirmation and pull out the dates and reference number."

## Good things to mention for sharper results

You don't have to, but including any of these helps Claude build a precise
query:
- **Who:** a sender or domain — "from stripe.com".
- **Folder:** "in my work inbox", "personal folder" (maps to a `maildir`).
- **When:** "this week", "last 30 days", "since June 1".
- **State:** "unread", "flagged", "not trashed".
- **Subject/keywords:** "about the renewal", "subject contains 'invoice'".

## What it will and won't do

- It **searches and reads** mail only — it does **not** send, delete, move, or
  modify anything in your maildir.
- Results are **bounded**: a sensible number of matches (default ~20) and email
  bodies are **capped (~8000 chars)** so they don't flood the conversation. Ask
  for "more results" or a specific message if you need to go deeper.
- It works on whatever `mu` has **indexed** — see below.

## Query syntax (optional reference)

Under the hood this is just [`mu find`](https://www.djcbsoftware.nl/code/mu/).
If you ever want to hand Claude an exact query:

| Query | Meaning |
|-------|---------|
| `flag:unread AND NOT flag:trashed` | all unread, non-trashed mail |
| `from:alice subject:invoice` | from alice, subject contains "invoice" |
| `maildir:/work/INBOX date:7d..now` | work inbox, last 7 days |
| `flag:flagged` | flagged messages |

See the [mu query manual](https://www.djcbsoftware.nl/code/mu/mu4e/Searching.html)
for the full syntax.

## If results look stale or empty

mu4e-mcp searches your **`mu` index**, not your live server. If recent mail is
missing, your index is behind — run `mu index` (or let mu4e/your mail sync do
it) and ask again. Also make sure `mu` is initialised (`mu init && mu index`).
