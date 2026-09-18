# RCL Knowledge Assistant — What's Been Built

A plain-language summary of everything done on this app so far. Read this if you want
to remind yourself how it works, or explain it to someone else (including in an
interview).

---

## 1. What this app does

Two pages, picked from the sidebar:

- **Ask a Question** — anyone types a question, the app finds the most relevant bits
  of your uploaded documents, and asks Google's Gemini AI to answer using only that
  information.
- **Admin Upload** — PIN-protected. Lets someone upload PDF/Word/txt documents, which
  get processed and added to the app's library so "Ask a Question" can use them.

---

## 2. The look and feel

- Blue-and-white theme matching Rayners College London's branding, set in
  `.streamlit/config.toml`.
- The real RCL logo, pinned to the top of the app via `st.logo(...)`.
- A gradient blue banner at the top of each page with a title and one-line description.
- Rounded buttons, card-style boxes around results, small icons next to buttons and
  section headers (📚 🛠️ 🔍 ➕ 🗑️) for visual polish.

---

## 3. How "Ask a Question" actually works (the RAG pipeline)

RAG = **R**etrieval-**A**ugmented **G**eneration. Instead of dumping every document into
the AI every time (slow, expensive, and breaks down as the library grows), the app
only sends the AI the small handful of passages actually relevant to the question.

Here's the pipeline, step by step:

1. **Chunking** — when a document is uploaded, its text is split into overlapping
   pieces of ~300 words each (with 50 words of overlap between pieces, so a fact
   sitting right on the boundary between two chunks doesn't get cut in half).
   Function: `chunk_text()`.

2. **Embedding** — each chunk gets turned into an "embedding": a list of ~3000 numbers
   that represents the *meaning* of that text, computed by Gemini's embedding API
   (`gemini-embedding-001`). Two pieces of text with similar meaning end up with
   similar numbers. Function: `embed_texts()`.

3. **Storage** — every chunk (its text, which document it came from, and its
   embedding) gets saved to a free cloud database called **MongoDB Atlas**, not a
   local file. This is the app's "document library." Because it lives on a real
   server out on the internet rather than this laptop's disk, **it survives closing
   the app, restarting your computer, and - importantly - restarting or redeploying
   the app once it's online.** Functions: `load_chunks()` / `add_chunks()`.

   (Earlier version note: this used to be a local file called `chunks_store.json`.
   That worked fine on a laptop, but would have been wiped every time the app
   restarted on free online hosting - see the setup steps below for why and how we
   moved it.)

4. **Search** — when someone asks a question, the question itself gets turned into an
   embedding the same way, and the app compares it against every stored chunk's
   embedding using a formula called **cosine similarity** (a way of measuring how
   close two lists of numbers are in "meaning"). The 5 closest-matching chunks are
   selected. Functions: `cosine_similarity()`, `search_chunks()`.

5. **Answer** — only those 5 chunks (not the whole library) get sent to Gemini's chat
   model along with the question, with instructions to answer only from that
   information. Function: `ask_gemini_stream()`.

6. **Streaming** — the answer appears on screen word-by-word as Gemini generates it
   (like ChatGPT/Claude), instead of the page freezing until the whole answer is
   ready. This uses Streamlit's `st.write_stream()`.

**Why there's no separate database software installed:** we originally tried
`chromadb`, a dedicated "vector database" library, for steps 3–4. It kept crashing
this specific machine (see below), so instead those steps are done with a plain JSON
file plus a small amount of everyday Python math — no extra software required, and it
does the exact same job at this app's scale (a handful of documents).

---

## 4. Bugs we hit and fixed along the way

| Problem | What was actually happening | Fix |
|---|---|---|
| App crashed the moment it tried to compute an embedding locally | A local AI model (used by `chromadb`'s default setup) hit a low-level crash specific to this machine — not a code bug | Switched to computing embeddings via the Gemini API instead of a local model |
| `KeyError: 'chroma_db'` on every click | `chromadb` was being reopened from scratch on every single button click (Streamlit re-runs the whole script each time), which isn't safe | (Later made irrelevant — see next row) |
| App crashed reading back real documents, even from a brand-new database | A second, separate crash — this time inside `chromadb`'s own storage engine, unrelated to the first crash | Removed `chromadb` entirely; replaced it with the plain JSON file + cosine similarity approach described above |
| "Something went wrong talking to the AI" / `429` errors | Google's free tier only allows 20 AI requests per minute, and testing repeatedly goes over that | This is expected, not a bug — the app shows a friendly message and you just wait ~30–60 seconds and try again |
| Blank/empty answer during streaming | The code checking for errors inside the streamed response wasn't actually reading them correctly | Fixed the event-handling code so a rate-limit or other error now shows a proper message instead of silence |
| Logo appeared twice | Two different pieces of code (`st.logo` and `st.sidebar.image`) were both drawing it | Removed the duplicate |

---

## 5. A security note

At one point a real Gemini API key got pasted into this chat by mistake. **If you
haven't already, go to [Google AI Studio](https://aistudio.google.com/apikey) and
regenerate/delete that key**, then only ever type keys directly into your own
terminal — never paste them into a chat window (with me or anyone else).

---

## 6. Developer tooling added

Ran `streamlit skills` to install an official Streamlit reference pack for AI coding
assistants (like me) — teaches best practices around caching, session state,
theming, etc. Installed globally on this computer (not just this project), so it'll
help with any future Streamlit project too. Doesn't change how your app behaves.

---

## 7. What's still a known limitation (not a bug — a deliberate trade-off so far)

- **Single shared PIN** for all admins, not individual logins — fine for a small
  team, not a scalable access-control system.
- **Images aren't read for text** — a `.png`/`.jpg` upload is only previewed, not
  OCR'd.
- **Free-tier rate limits** (20 requests/minute) apply to both asking questions and
  uploading documents, since both use the same Gemini API key.

*(Resolved: documents used to live in a local file that would have been wiped on
free online hosting — fixed by moving storage to MongoDB Atlas, see section 9.)*

---

## 8. Key files in this project

```
app.py                       — the whole app (one file)
.streamlit/config.toml       — colour theme
.streamlit/secrets.toml      — your MongoDB connection string (NEVER share/commit this)
assets/rcl_logo.png          — the RCL logo used in the UI
migrate_to_mongo.py          — one-time script to move old local chunks into MongoDB
admissions.txt, etc.         — your own test documents
```

---

## 9. Setting up MongoDB Atlas (one-time, needed before documents will save)

The app now stores documents in a free cloud database instead of a local file, so
they survive even if you deploy this online later. You need to create the free
account yourself (nobody else can do this step for you):

1. Go to https://www.mongodb.com/cloud/atlas/register and sign up (free, no card
   needed).
2. Create a new project, then click **"Build a Database"** and choose the **free
   M0** tier.
3. When asked for a database user, set a username and password (write the password
   down somewhere safe).
4. Under **Network Access**, click **"Allow access from anywhere"** (`0.0.0.0/0`) -
   needed so both your laptop and, later, Streamlit Cloud can reach it.
5. Click **"Connect"** → **"Drivers"**, and copy the connection string shown (starts
   with `mongodb+srv://...`).
6. Open `.streamlit/secrets.toml` in this project and replace the placeholder with
   that connection string, filling in the real username/password you chose in step 3.
7. (Optional, one-time) Run `python migrate_to_mongo.py` to copy your existing
   `admissions.txt` etc. from the old local file into MongoDB, so you don't have to
   re-upload them.
8. When you eventually deploy to Streamlit Community Cloud, paste the same
   `MONGO_URI` value into that app's **Settings → Secrets** there too - the local
   `secrets.toml` file never gets uploaded (see `.gitignore`).
