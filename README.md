# BRAOU Gondi Vaani

**Expert-voiced Gondi listening and expression library** for Dr. B. R. Ambedkar Open University, Hyderabad.

Learners search, browse and listen to expert-validated Gondi words, greetings, phrases and sentences with Telugu and English meanings. Authorised staff add entries, record or upload authentic audio, bulk-import CSV + ZIP, review and publish. There is no synthetic Gondi voice and no AI dependency. Nothing in the code invents Gondi text, translations or pronunciations.

| Page | Who | What |
|---|---|---|
| `https://<server>/` (`frontend/index.html`) | Learners | Search Gondi/Telugu/English, category filters, play/replay, 0.75× / 1× speed, previous/next, browser-local favourites |
| `https://<server>/admin` (`frontend/admin.html`) | Administrators, contributors, reviewers | Add/edit/archive, microphone recording and upload, CSV + ZIP bulk import with dry run, review → approve → publish, speakers and consent, users, reports, backups |

---

## 1. Architecture

```
Browser ──HTTPS──▶ nginx (container) ──HTTP──▶ FastAPI / Uvicorn (container, non-root)
                    TLS, HTTP/2, gzip,          │  SQLAlchemy ─▶ SQLite  (/data/gondi_vaani.db)
                    upload limits               │  audio files ─▶ /data/audio  (never served directly)
                                                └─ ffmpeg: validates audio, converts browser recordings to MP3
```

* **Backend:** Python 3.12, FastAPI, Pydantic, SQLAlchemy 2, Alembic migrations, SQLite (WAL). The schema is ready to migrate to PostgreSQL by changing `GV_DATABASE_URL`.
* **Frontend:** HTML5, CSS3 and vanilla JavaScript. Fonts are self-hosted: Noto Sans/Serif, Noto Sans Telugu, Noto Sans Masaram Gondi and Noto Sans Gunjala Gondi (SIL OFL). The pages make no third-party requests.
* **Audio:** learners can only fetch the *approved default recording of a published expression*, through `/api/public/audio/{id}` (with HTTP Range support for iOS). Everything else returns 404.

### Data model

| Table | Purpose |
|---|---|
| `expressions` | `expression_code`, `gondi_text`, `transliteration`, `telugu`, `english`, `category_id`, `variety`, `script`, `usage_note`, `provenance`, `status`, timestamps |
| `recordings` | per-expression audio: `speaker_id`, random `stored_filename`, original name, `mime_type`, `duration`, `status` (pending/approved/rejected), `is_default` |
| `speakers` | `display_name`, `variety`, `consent_scope`, `consent_date`, `reuse_restrictions`, `attribution` |
| `users` | `username`, Argon2id `password_hash`, `role`, `active` |
| `categories` | `name`, `sort_order` |
| `reviews` | workflow history: who submitted, approved, rejected (with comments), published |
| `import_jobs` | every dry run and import, with the full row-by-row report |
| `user_sessions`, `audit_logs` | server-side sessions; who did what and when |

### Editorial workflow

```
draft ──submit──▶ in_review ──approve──▶ approved ──publish──▶ published
  ▲   (contributor/admin)   (reviewer/admin)       (admin only)      │
  └────────── reject with comments (reviewer/admin) ◀───────────────┘ unpublish (admin)
```

The backend enforces these rules:

* **Submit** needs expert-supplied Gondi text and a default recording.
* **Approve** needs Gondi text, a meaning, a default recording, and a speaker whose **consent scope and consent date** are recorded. Approving also approves the pending default recording.
* **Publish** is admin-only and a separate action from approval.
* If a reviewed entry's *text* is edited (Gondi, transliteration, Telugu, English, variety or script), it goes back to **draft** and leaves the learner library until it is reviewed again. Changing only the category does not.
* A live default recording cannot be deleted, rejected or left without a consenting speaker.

| Role | Can |
|---|---|
| **Contributor** | create entries, edit drafts, add recordings to drafts, register speakers, bulk import, submit for review |
| **Reviewer** | listen and verify, approve or reject recordings and entries (with comments), set speakers |
| **Administrator** | everything above, plus publish/unpublish, archive/delete, users, categories, audit log, backups |

---

## 2. Project tree

```
gondi-vaani/
├── app/
│   ├── main.py            FastAPI app, security headers, static pages
│   ├── config.py          settings (GV_* environment variables)
│   ├── database.py        engine/session (SQLite pragmas: WAL, foreign keys)
│   ├── cli.py             create-admin, reset-password, seed-categories, backup, restore
│   ├── models/            SQLAlchemy models
│   ├── schemas/           Pydantic schemas
│   ├── routers/           public, auth, expressions, recordings, imports, catalog, users, reports
│   ├── services/          audio, importer, workflow, backup, audit, serializers
│   └── security/          Argon2 passwords, DB sessions, CSRF + role dependencies
├── frontend/              index.html, admin.html, css/, js/, fonts/
├── migrations/            Alembic (0001_initial_schema)
├── tests/                 pytest suite (41 tests)
├── deployment/            docker-entrypoint.sh, nginx/gondi-vaani.conf, certs/
├── scripts/               install-ubuntu.sh, gv, backup.sh, restore.sh, letsencrypt.sh, make-selfsigned-cert.sh, make-test-audio-zip.py
├── samples/gondi-vaani-100-prompts.csv   BRAOU's 100 recording prompts (Telugu/English; Gondi blank)
├── Dockerfile, docker-compose.yml, .env.example
└── requirements.txt, requirements-dev.txt
```

---

## 3. Deploy on Ubuntu with Docker

These steps target Ubuntu 22.04 or 24.04, with 2 GB RAM and about 10 GB of disk. Ports 80 and 443 must be reachable.

### 3.1 Copy the project and run the installer

```bash
sudo mkdir -p /opt/gondi-vaani
sudo tar -xzf gondi-vaani.tar.gz -C /opt      # or: git clone … /opt/gondi-vaani
cd /opt/gondi-vaani
sudo ./scripts/install-ubuntu.sh gondivaani.braou.ac.in    # your server's DNS name
```

The installer:

* installs Docker Engine and the Compose plugin from Docker's official repository, if they are missing
* creates `.env` from `.env.example`
* creates a temporary **self-signed** certificate
* opens ports 80 and 443 in `ufw`, if `ufw` is active
* runs `docker compose up -d --build`

When the containers start, the app container applies the database migrations automatically.

**Manual equivalent:**

```bash
cp .env.example .env
./scripts/make-selfsigned-cert.sh gondivaani.braou.ac.in
docker compose up -d --build
docker compose ps        # app should become "healthy"
```

### 3.2 Create the first administrator

```bash
sudo ./scripts/gv create-admin --username admin --name "Dr. … (Coordinator)"
sudo ./scripts/gv seed-categories      # optional starter categories (editable later)
```

Passwords need at least 10 characters, with upper- and lower-case letters and a digit.

Sign in at `https://<server>/admin`. Then under **Users**, add the contributors and reviewers.

### 3.3 Set up HTTPS properly

HTTPS is required: browsers allow microphone recording only on HTTPS pages.

**Option A: Let's Encrypt.** This needs a public DNS name pointing at the server, with port 80 open.

```bash
sudo ./scripts/letsencrypt.sh gondivaani.braou.ac.in it-admin@braou.ac.in
# renewal, e.g. in root's crontab:
17 3 * * 1 cd /opt/gondi-vaani && ./scripts/letsencrypt.sh gondivaani.braou.ac.in it-admin@braou.ac.in renew
```

**Option B: the university's own certificate.** Copy the full chain and the key into the certs folder, then reload nginx:

```bash
cp fullchain.pem deployment/certs/fullchain.pem
cp privkey.pem deployment/certs/privkey.pem
chmod 600 deployment/certs/privkey.pem
docker compose exec nginx nginx -s reload
```

**Already running nginx or Apache on the host?** Remove the `nginx` service from `docker-compose.yml`. Add `ports: ["127.0.0.1:8000:8000"]` to `app`, then proxy to `http://127.0.0.1:8000` using the `location /` block from `deployment/nginx/gondi-vaani.conf`. Keep `client_max_body_size 320m` and the `X-Real-IP` and `X-Forwarded-*` headers.

### 3.4 Everyday operations

| Task | Command |
|---|---|
| Status / health | `docker compose ps` · `curl -k https://localhost/healthz` |
| Logs | `docker compose logs -f app` (nginx: `docker compose logs -f nginx`) |
| Restart | `docker compose restart app` |
| Update to a new version | copy the new code, then `docker compose up -d --build` (migrations run on start) |
| Reset a password | `sudo ./scripts/gv reset-password --username someone` |
| List users | `sudo ./scripts/gv list-users` |
| Stop everything | `docker compose down` (data in `./data` is kept) |

All data lives in **`./data/`** on the host:

* `gondi_vaani.db`: the database
* `audio/`: recordings
* `backups/`
* `imports/`: staged previews

### 3.5 Backups and restore

A backup is a single `.tar.gz`. It holds a consistent snapshot of the database, every audio file and a manifest.

```bash
sudo ./scripts/backup.sh                    # writes data/backups/gondi-vaani-backup-YYYYMMDD-HHMMSS.tar.gz, keeps newest 14
```

To schedule a nightly backup, run `sudo crontab -e` and add:

```
30 2 * * * cd /opt/gondi-vaani && ./scripts/backup.sh >> /var/log/gondi-vaani-backup.log 2>&1
```

Copy the backups off the server as well, for example to the university's backup storage with `rsync` (there is a commented line in `backup.sh`). Administrators can also create and download backups from **Reports → Backups**.

**Restore.** The app stops while you restore. Before replacing anything, the current state is saved as a new safety backup.

```bash
sudo ./scripts/restore.sh data/backups/gondi-vaani-backup-20261030-023000.tar.gz
```

Test a restore on a spare machine once before the institutional release.

---

## 4. Bulk import (CSV + ZIP)

**CSV columns.** The file must be UTF-8. In Excel, use *Save As → CSV UTF-8*.

* Required: `expression_id, category, english, telugu, gondi_text, variety, script, audio_filename`
* Optional: `transliteration, usage_note, speaker`. The `speaker` value must match a registered speaker's name.

```
expression_id,category,english,telugu,gondi_text,variety,script,audio_filename
GON-001,Greetings,Hello,నమస్కారం,,Adilabad,,GON-001.mp3
GON-002,Greetings,How are you?,మీరు ఎలా ఉన్నారు?,,Adilabad,,GON-002.mp3
```

**Import steps:**

1. **Bulk import** in the console: choose the CSV and a ZIP of the audio files, then click **Check files (dry run)**. Nothing is saved at this stage. The preview shows every row with its errors and warnings:
   * duplicate IDs (within the file or already in the library)
   * missing or unused audio files, unsupported or damaged audio, recordings that are too long
   * bad UTF-8, wrong or missing columns, malformed rows
2. When every row is valid, click **Import**. You can also tick *skip the rows with errors* to import only the valid rows. The import runs as one database transaction: if anything fails, nothing is saved and any audio already written is removed.
3. Every imported entry is a **draft**, with its recording *pending*. Experts then add the Gondi text, reviewers approve, and administrators publish.

**Safety checks:**

* Unsafe ZIP entries reject the whole upload: absolute paths, `..`, symbolic links, encrypted entries, zip-bomb ratios and oversized files.
* Audio files are matched by file name only, read from memory and never extracted to disk.
* CSV content is never executed. Cells beginning with `=`, `+`, `-` or `@` are escaped in exports.

**Trying the importer with audio but no real recordings.** `python3 scripts/make-test-audio-zip.py samples/gondi-vaani-100-prompts.csv test.zip` makes **test beeps**. They are for testing only; never publish them.

### Loading the 100 recording prompts (text first, audio later)

`samples/gondi-vaani-100-prompts.csv` holds the current working Telugu/English text for 100 prompts. Its extra columns `review_status` and `recording_notes` are accepted and kept in each entry's provenance note.

* **In the console:** go to **Bulk import**, choose the CSV, leave *Audio ZIP* empty and click **Check files**, then **Import all 100 rows**.
* **On the server:**
  ```bash
  cp samples/gondi-vaani-100-prompts.csv data/
  sudo ./scripts/gv import-csv /data/gondi-vaani-100-prompts.csv --as admin            # dry run
  sudo ./scripts/gv import-csv /data/gondi-vaani-100-prompts.csv --as admin --commit
  ```
  `sudo ./scripts/gv seed-categories` creates the same seven categories in the same order. It is optional, because the import creates them anyway.

This gives you 100 drafts. Open the first, record it with the microphone, then use **Next ›** to work through them in one session; the chosen speaker stays selected.

**Replacing the text later.** Import the revised file with mode **Add new and update existing**, or add `--update` on the command line:

* rows with an existing `expression_id` are updated, and new IDs are added;
* blank cells never erase existing values, so Gondi text and recordings entered in the meantime are kept;
* the preview lists exactly which fields change on each row;
* if the text of an entry already in review, approved or published changes, it goes back to draft for re-review.

Contributors can update draft entries only.

---

## 5. Moodle (MC1 / MC2)

* **URL resource:** add a *URL* activity pointing at `https://<server>/`.
* **Deep links:**
  * `https://<server>/?category=Greetings` opens one category.
  * `https://<server>/?q=water` opens a search.
  * `https://<server>/#GON-001` opens one expression, ready to play.
* **Embedding:** if Moodle should show the page inside a frame (*Display: Embed*), allow the Moodle origin in `.env`, then run `docker compose up -d`:
  ```
  GV_FRAME_ANCESTORS=self https://lms.braou.ac.in
  ```
  The editorial console can never be framed.
* **LTI** can be added later. The learner API (`/api/public/...`) is read-only and needs no login.

---

## 6. Local development and tests (without Docker)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt          # ffmpeg recommended: sudo apt install ffmpeg
export GV_ENVIRONMENT=development GV_COOKIE_SECURE=false \
       GV_STORAGE_DIR=./storage GV_DATABASE_URL=sqlite:///./storage/gondi_vaani.db
alembic upgrade head
python -m app.cli create-admin --username admin
uvicorn app.main:app --reload
# open http://localhost:8000/ and http://localhost:8000/admin   (API docs: /api/docs in development only)

pytest -q                                    # 41 tests
```

In the Docker image, the tests can run with `docker compose run --rm app sh -c "pip install pytest httpx && pytest -q"`. That needs the `tests/` folder mounted or copied in.

**What the tests cover:**

* Learners cannot reach drafts or unapproved audio by URL or API.
* The full contributor → reviewer → admin flow, with role separation.
* Audio streaming with HTTP Range requests.
* Editing reviewed text sends the entry back to review.
* Protection of the live recording.
* Fake, oversized and over-long audio is rejected; WebM recordings convert to MP3.
* A 100-row CSV + ZIP previews and imports.
* Row-level import errors; zip-slip, symlink and malformed-ZIP rejection; formula escaping.
* Login: cookie flags, CSRF, lockout, session revocation, the last-admin guard.
* Security headers and storage that is not publicly reachable.
* Backup and restore round-trip.
* Migrations match the models.

---

## 7. Security summary

* Passwords are hashed with **Argon2id**.
* Sessions are **server-side**: the cookie holds a random token, stored only as a hash.
  * The cookie is `HttpOnly; Secure; SameSite=Strict`.
  * Sessions expire after 8 hours (configurable).
  * Sessions are revoked on role change, deactivation or password change.
* Every state-changing request needs an **`X-CSRF-Token`** header that matches the session.
* Each **role is checked on the backend** for every endpoint.
* Login locks after 5 failures per account in 15 minutes, with a looser limit per IP so a shared campus IP is not locked out.
* **Content-Security-Policy** allows `'self'` only, with no inline scripts. The console is sent with `frame-ancestors 'none'` and `X-Frame-Options: DENY`. HSTS and nosniff are on, and `Permissions-Policy` allows only the microphone.
* **Audio handling:**
  * File type is identified from the file's signature, never the client's MIME type.
  * Files are decoded by ffprobe; size and duration limits apply.
  * Files get random internal names, are stored outside any public folder, and are streamed only through the access-checked endpoint.
* **Container hardening:**
  * The app runs as uid 10001 on a read-only root filesystem, with `no-new-privileges` and minimal capabilities.
  * The app port is reachable only by nginx.
* Every sign-in, edit, review decision, import and backup is recorded in the **audit log** (Reports → Activity log).

## 8. Ethical and quality safeguards

* Gondi text, translations and pronunciations come only from experts. The importer leaves `gondi_text` blank until an expert supplies it, and submission is blocked until it is filled in.
* Nothing is published without speaker consent (scope and date), a reviewer's approval and an administrator's decision. The speaker's chosen credit line is shown to learners.
* Learners always hear the authentic recording. Browser recordings are converted to MP3 only so that they play on every phone; the original file is kept.
* Before institutional release:
  * test a restore
  * replace the self-signed certificate
  * review the starter category names with the language experts
