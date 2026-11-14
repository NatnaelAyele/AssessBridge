# AssessBridge

AssessBridge uses one sign-in page for the three SRS roles: Candidate, Employer Company Personnel, and Employer Company Representative Auditor. The server determines the role after authentication, so the page has no role selector.

## Project structure

```text
AssessBridge/
  frontend/
    index.html           # Shared login page
    employer.html        # Employer dashboard
    auditor.html         # Auditor workspace
    candidate.html       # Candidate workspace
    exam-entry.html      # Fresh-session exam entry
    register.html        # Candidate self-registration
    verify-email.html    # Candidate email verification
    css/                 # Page styles
    js/                  # Page interactions
  backend/
    app/                 # FastAPI routes, database access, and security
    tests/               # Authentication, invitation, and exam integration tests
    schema.sql           # MySQL tables
    init_db.py           # Initialize the database
    fetch_identity_models.py # Download pinned YuNet and SFace models
    create_user.py       # Add users from a terminal
    set_candidate_email.py # Register email for an existing candidate
  .env.example           # Local configuration template
  README.md
```

## Run locally

On Windows PowerShell, from the project root:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
& .\.venv\Scripts\python.exe backend\fetch_identity_models.py
Copy-Item .env.example .env
```

Set `DB_PASSWORD` in `.env` to the MySQL password, replace `JWT_SECRET` with a random secret of at least 32 characters, and set `RECORDING_KEY` to a random URL-safe base64-encoded 32-byte key. The local `.env` is ignored by Git. The configured connection is `root@127.0.0.1:3306/assessbridge`.

Fayda card matching also requires the open-source Tesseract OCR executable and its English language data on the FastAPI server. On Windows, install Tesseract using the [UB Mannheim Windows build](https://github.com/UB-Mannheim/tesseract/wiki) or `winget install --id UB-Mannheim.TesseractOCR --exact`. AssessBridge finds it on `PATH`, in `C:\Program Files\Tesseract-OCR`, or in this project's ignored `var\tesseract` directory. Set `TESSERACT_CMD` in `.env` if it lives elsewhere. On Linux, install the operating system's `tesseract-ocr` package. `fetch_identity_models.py` verifies pinned SHA-256 hashes before using the OpenCV models. Re-run `backend/init_db.py` on existing databases to add the identity-check table.

```powershell
& .\.venv\Scripts\python.exe backend\init_db.py
& .\.venv\Scripts\python.exe backend\create_user.py candidate1 candidate --email candidate@example.com
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
```

Open `http://127.0.0.1:8000/`. Candidates can create their own account at `/register` using their invitation email, then activate it with the emailed verification link. SMTP must be configured for registration. The user creation command is still available for administrator-created accounts and prompts for a password. Candidate accounts require a registered email that matches the email on their assessment invitations. To assign an email to an existing candidate account, run `& .\.venv\Scripts\python.exe backend\set_candidate_email.py USERNAME EMAIL`. Use `employer_personnel` or `auditor` in place of `candidate` to create the other roles. Open the page through FastAPI so its API requests resolve; opening the HTML file directly will not connect to the API.

Auditors sign in through the same page and are sent to `/auditor`. The route and `/api/auditor/*` endpoints require an active auditor session. Every auditor view and API, including Publication history, candidate reports, recordings, identity images, audit completion, and publication, requires the assessment's scheduled end time to have passed. A publication never creates an exception to the closed-only access rule. Assessment auditing provides exam search and audit-status filters, a candidate table, recorded grades and final results, reviewer details and history, identity images and similarity scores, monitoring coverage, full recordings, and flag evidence with highlighted looping playback. Review decisions, grading, and findings are read only for auditors. Mark as audited stores the authenticated auditor, UTC timestamp, review revision, and evidence snapshot. Before publication, changed review or evidence records require a fresh audit. A reviewer decision and finished monitoring processing are required before marking a case audited; missing evidence is shown plainly. Published audits stay final even after evidence expires. Publication history shows one final entry per closed exam, with View reports providing read-only access. There is no unpublish or reversal workflow. Overview is empty for now. Run `backend/init_db.py` to update an existing installation. To create an auditor account, run `& .\.venv\Scripts\python.exe backend\create_user.py auditor1 auditor` and enter a password when prompted.

Employer personnel are redirected to `/employer` after sign-in. That dashboard has an overview, a six-step assessment editor, a searchable and filterable list of saved assessments with read-only question and answer-key details, and working Review cases and Results & reports tabs. Rules & identity offers predefined prohibited items and a choice of Fayda ID card (default) or Other ID cards. The selected policy is stored with the assessment and enforced by the server. The Schedule step sets the access window. Final review summarizes the editor, checks required information, and has the **Confirm and send invitations** action. A successful send saves the assessment, emails calendar invitations with assessment-specific credentials, and shows a confirmation dialog. New invitations use a five-character exam username and eight-character exam password, SEB configuration password, and emergency quit code. Older invitations keep their original credentials. Closing the dialog clears the editor for another exam. Assessment detail shows the schedule, questions and answer keys, invited candidates, and their progress. The editor has no separate draft save yet. The local development checklist is excluded from version control.

Candidates can open `/exam-entry` in a fresh browser session, sign in to their candidate account, and then enter the exam-specific username and password from the invitation email. The registered candidate account email must match the invitation email. Existing matching accounts are bound when invitations are created; otherwise a matching account is bound on the first successful exam entry. Other candidate accounts cannot use the invitation. The `/candidate` workspace offers Enter exam and History. During the access window, the assessment opens a full-screen exam room. The room requires agreement to the identity-check and recording notice before advancing, and the decision is stored in MySQL. Next, the candidate grants camera and microphone access, sees a local camera preview and microphone activity meter, and can continue only while both media tracks are live. The candidate captures the photo ID required by the assessment and then their face. The server reads the English card text, compares the card name with the invitation, and compares the card portrait with the camera image. The candidate may retake unclear images; a new exam attempt cannot start until the server records a pass for that invitation and candidate. The candidate then reads the exam instructions and selects Start exam. From then until submission or exit, the browser records both video and audio in short pieces, uploads them to authenticated FastAPI endpoints, and shows recording health. Questions appear one at a time; Next advances one question, Previous returns one question, and answers save automatically in MySQL. The visible countdown uses a server-set deadline. The final question offers a confirmed manual submission; expired attempts submit automatically, including when the page is closed. Recording stops when the attempt ends, and missing or interrupted pieces remain marked incomplete. Candidate History shows the review outcome, original multiple choice score, written question score, final score, reviewed time, and submitted time only from the latest published result snapshot. Before publication, scores and review decisions are withheld by the API. Written answers can be graded by the assessment owner during review.

The exam's auditor candidate view provides Approve and publish results after its schedule closes. Every candidate in the full roster must have a completed, current audit before publication; missing attempts and stale audits block it. Confirmation requires a remark. `POST /api/auditor/assessments/{id}/publish` rechecks the roster and audit versions, locks the exam and sessions, and atomically records the authenticated publisher, timestamp, remark, and each candidate's approved results. Publication is final: reviewers cannot save further grades, decisions, findings, or invitation changes. Publication and review writes share an exam lock so concurrent requests cannot bypass this rule. Retries return the existing publication, and a unique database constraint prevents another final entry for the same exam. The migration keeps the latest existing publication final and marks earlier duplicates as legacy records, preserving their result snapshots internally while omitting them from history and candidate results. Run `backend/init_db.py` to update an existing installation.

The card-to-person check does **not** validate a card against its issuer's records and does **not** prove camera liveness. It is a local name and portrait comparison. Card capture samples several camera frames and keeps the sharpest. OpenCV combines colour segmentation and brightness, saturation and colour edges to locate and straighten pale or coloured cards. Rounded corners, small edge occlusions and perspective are handled without requiring one exact four-point contour. Fayda retains a flag-based crop fallback. Fayda's layout must show a recognizable heading or flag. The crop keeps a margin around the title and flag, and flag detection handles several camera scales. Tesseract reads the name area using grayscale, lighting correction, adaptive thresholding, and alternative text segmentation. Name reading retains word positions and confidence. A shared matcher groups full-name or given-name/surname fields, supports separate columns, widely separated labelled fields, wrapped lines and either field order, and excludes unrelated fields and portrait OCR noise. It splits OCR-merged columns and can re-read individual text cells when whole-card reading fails. An unreadable labelled name field cannot be silently omitted, and confidence from unrelated captions does not by itself establish name readability. Common slash-separated bilingual captions are treated as labels rather than name values. Every invitation name part needs a distinct match within the card holder's name; case, accent marks and punctuation are normalized. Invitations may use a shorter combination of the ID name, in any order, provided at least two distinct full name parts are supplied. For example, an ID showing Natnael Ayele Eticha can match Natnael Ayele or Natnael Eticha. Extra name parts printed on the ID are allowed; every part supplied in the invitation must still match. Short parts require exact spelling and longer parts allow limited OCR edits. The overall name-match threshold remains 0.85. Single-name invitations, initials, invitation parts absent from the ID, and arbitrary transliteration differences do not automatically pass. Unreadable print and a readable but different name produce separate results. Face comparison runs only after those steps pass. It uses original card pixels masked to the selected or detected outline, keeping document straightening for text reading. After the full-image face and multiple-person checks, YuNet refines landmarks in a local crop at a consistent scale for both the portrait and camera face; a refinement must overlap the selected face. SFace uses that alignment for its cosine comparison. The acceptance threshold remains 0.40, and crop selection is never based on trying to obtain a passing similarity score. After capture, Adjust card outline lets the candidate place four corner markers with a pointer or arrow keys. The original image and normalized corners go to the server, which validates geometry and still enforces the assessment's card policy, name match and face match. If reading the straightened manual selection fails, the reader also tries the original pixels inside that same selection, with surrounding text masked out. This avoids relying only on glyphs altered by perspective interpolation. The original image remains the encrypted evidence and the check records manual_crop when that route is used. The candidate page shows the assessment title, check stage, attempt number, and check time. Other ID cards use outline detection for pale or coloured cards in landscape or portrait orientation and read name fields across the card. Fayda first tries its usual name area, then the full card with the same spatial matcher. They require a readable matching name and matching portrait without a country, flag or issuer-heading requirement. Other ID cards also have a conservative TD3 passport-name fallback: it locates and deskews two aligned 44-glyph MRZ rows, identifies chevron separators using both repeated trailing glyphs and their shape, confirms the passport document type, and reads every name segment with agreeing OCR passes. Broken strokes can be grouped into one character by their image columns, while the two-row, 44-character structure is still required. Separator checks distinguish blurred A/C/K letters from chevrons. When necessary, a second image scale is tried for OCR agreement; disagreement about the unused issuer code does not block name reading. Unreadable segments and ambiguous readings are rejected; a clearly read complete MRZ name is compared using the same shorter-invitation rule. The invitation is used only for comparison after extraction. This is name reading, not checksum, issuer, chip or document-authenticity validation. No passport number or date is OCRed by that fallback. Both options currently use English OCR for Latin-script names. This is a photo-card layout check, not a trained authenticity classifier; arbitrary foreign scripts and every document layout are not supported. The current thresholds and capture guidance still need testing across real cards before production use.

The server stores encrypted recording pieces in `RECORDINGS_DIR` (default `var/recordings`), outside the public frontend. MySQL links each recording and piece to its exam session, invitation, and candidate. Authorized personnel who own the assessment can play the available footage; every metadata or playback access is logged. Media is deleted after `RECORDING_RETENTION_DAYS` (default 30) by a background task; exam answers and audit records have separate retention. Back up `RECORDING_KEY` securely: losing it makes stored footage unreadable. The linked privacy and data-control pages describe the development setup; production wording, retention, and location require approval before real use.

The card and face images are encrypted in MySQL with a key derived from `RECORDING_KEY`. Their latest check record is bound to the candidate account, invitation, current sign-in, and exam entry. Each check also creates a metadata-only history row with its stage, time, and scores; it does not create another image copy. A new exam entry or an SEB exit before starting requires another card and face check; the employer dashboard shows the last check result. The exam session points to the same invitation. The latest images are cleared after 30 days, while check history and scores remain. The employer dashboard shows only the status, not the images. Losing `RECORDING_KEY` also makes retained identity images unreadable.

To enable email delivery, set `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` (`starttls` or `ssl`), `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_SENDER` in the ignored `.env`. Until these are configured, the send endpoint returns a setup error and does not save or send invitations. The email includes the access window and exam credentials.

After entering SMTP settings, run `& .\.venv\Scripts\python.exe backend\test_smtp.py` and enter an email address you control. This sends one setup test message, without creating an assessment or sending candidate invitations. The script never prints the SMTP password.
Restart the FastAPI server after changing `.env` so the running app reads the new SMTP settings.
For local invitation and email verification links, `EXAM_ENTRY_URL` defaults to `http://127.0.0.1:8000/exam-entry`. Set it to the public HTTPS address when deploying. Run `backend/init_db.py` after updating an existing installation so invitations gain their account binding column and registration can store verification tokens.

Publication history supports case-insensitive exam-name/code search with highlighted matches and Published date filters: Any time, Today, Last 7 days, Last 30 days, and Custom range. Date boundaries use EAT and include both selected custom dates. Search and date filters combine before pagination. View results opens the publication summary and remark plus candidate scores from the saved publication snapshots; View report opens the existing read-only candidate report with a return to published results. History and published candidate tables become cards on small screens.

## Authentication API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/auth/login` | Check username/password and set an HTTP-only session cookie |
| POST | `/api/auth/register-candidate` | Create an inactive candidate account and email a verification link |
| POST | `/api/auth/verify-email` | Activate a candidate account using a valid emailed token |
| POST | `/api/auth/resend-verification` | Send a new link for an inactive candidate account |
| GET | `/api/auth/me` | Return the authenticated user and role |
| GET | `/api/auth/authorize/{role}` | Return 200 for the matching role, 403 otherwise |
| POST | `/api/auth/logout` | Revoke the session and clear its cookie |
| POST | `/api/employer/invitations` | Save an assessment and send its candidate calendar invitations |
| GET | `/api/employer/assessments?page=1` | List saved assessments for the signed-in employer, 20 per page |
| GET | `/api/employer/assessments/{id}` | Show one employer-owned assessment and its roster |
| GET | `/api/employer/assessments/{id}/invitations` | Read invitation delivery status for an employer-owned assessment |
| POST | `/api/candidate/exam-entry` | Verify a signed-in candidate's assessment-specific invitation credentials |
| GET | `/exam-room` | Open the room during its access window after verifying exam credentials |
| GET | `/api/candidate/exam-room` | Read the room's assessment and consent state |
| POST | `/api/candidate/exam-room/consent` | Record the candidate's agreement to the current draft policies |
| GET | `/api/candidate/identity` | Read the invitation's ID card-match status |
| POST | `/api/candidate/identity/verify` | Compare captured card and face images before the exam starts |
| GET | `/api/candidate/exam/session` | Read attempt state, current question, answer, and server deadline |
| POST | `/api/candidate/exam/start` | Start or resume the candidate's single assessment attempt |
| POST | `/api/candidate/exam/answer` | Save the current question's answer |
| POST | `/api/candidate/exam/navigate` | Move one question forward or backward |
| POST | `/api/candidate/exam/submit` | Submit from the final question |
| GET | `/api/candidate/exam/recording` | Read the candidate's recording status |
| POST | `/api/candidate/exam/recording/parts` | Start a video/audio recording part |
| POST | `/api/candidate/exam/recording/parts/{part_id}/chunks/{sequence}` | Upload one ordered media piece (maximum 8 MiB) |
| POST | `/api/candidate/exam/recording/parts/{part_id}/finish` | Finalize a part or mark interruption |
| POST | `/api/candidate/exam/recording/finish` | Finalize the recording after the attempt ends |
| GET | `/api/employer/assessments/{id}/invitations/{invitation_id}/recording` | Read owned recording metadata |
| GET | `/api/employer/assessments/{id}/invitations/{invitation_id}/monitoring` | Read owned monitoring events, capabilities, jobs, and coverage (`after_id`, `limit`) |
| GET | `/api/employer/assessments/{id}/invitations/{invitation_id}/recording/parts/{part_id}` | Stream protected footage with byte-range support |

Passwords are stored as bcrypt hashes. Sessions use signed JWTs backed by MySQL session records so sign-out invalidates them. Authentication events and role denials are recorded in `auth_audit`. For an HTTPS deployment, set `COOKIE_SECURE=true` and serve the application over TLS.

Exam questions sent to candidates omit answer keys. MySQL stores one attempt and its answers per invitation. The deadline is the earlier of the assessment duration and the access-window close. FastAPI checks the deadline on exam requests and sweeps expired attempts every five seconds while the server is running; startup catches attempts that expired while the server was stopped.

To run the database integration tests, install `backend/requirements-dev.txt` and run `& .\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v`. Tests create temporary users and remove them afterward. The identity engine tests require Tesseract and the downloaded face models.


## AI exam monitoring

Monitoring applies to new exam attempts after agreement to `recording-monitoring-3`.
It never submits, disqualifies, changes an answer, or changes a score. Existing SEB
exit/connection-loss rules remain independent. The owner-authorized review workspace presents evidence and records human findings and dispositions.

Setup (inside the existing project environment):

```powershell
.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.venv\Scripts\python.exe backend/fetch_monitoring_models.py
.venv\Scripts\python.exe backend/init_db.py
```

FastAPI automatically supervises a separate local monitoring process. Set
`MONITORING_AUTO_START=false` if running the worker separately:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m app.monitoring.worker
```

On Linux/macOS install FFmpeg and make it available on PATH or set `FFMPEG_CMD`.
Logs are in `var/monitoring-worker.log` for the managed process. The monitoring API
reports `worker_online`, per-part progress/error state, model version, and coverage.
`MONITORING_CONCURRENCY=2` permits two concurrent recording parts locally; other
parts queue durably. It is not a guaranteed real-time service under arbitrary load.
Benchmark and size deployment capacity before increasing this setting.

Each attempt freezes its monitoring policy and an encrypted copy of the verified
selfie. Recording parts create durable MySQL jobs. The worker streams authenticated,
checksum-checked recording chunks in order into FFmpeg over pipes, without creating
plaintext media files. It treats all chunks in a part as one container stream, with
separate decoders for 2-fps video and 16-kHz mono audio. WebM and fragmented MP4 have
real integration tests. An undecodable stream is marked incomplete, never clean.
A crashed worker replays the part; stable observation/event keys prevent duplicates.
The API and answer-saving path do not perform model inference.

Enabled observations:

* Identity mismatch against the fixed verified selfie, only when the detected face
  is large, sharp, and sufficiently frontal. Missing/unusable faces are separate.
* Additional person (always enabled), using faces and YOLOX person detections.
* Visible phone, book, tablet, or calculator only when that item is prohibited by
  the assessment. Books use YOLOX; tablets and calculators use the approved
  RT-DETR-R18 Objects365-v2 model. A continuous 1.5-second sighting opens an
  observation for human review. The additional detector runs only for exams that
  prohibit Tablet or Calculator. Already-started attempts keep their frozen policy.
* Coarse head-turn/downward changes relative to the verified selfie's landmark
  geometry, sustained or repeated. This is not exact eye gaze or screen tracking.
* Dark/covered/washed-out camera, unusable face, suspected frozen video, and major
  framing changes. Frozen-frame checks do not authenticate liveness or prove replay.
* Sustained/repeated speech, clipped audio, and exact digital silence. Ordinary
  quiet is not misconduct. Speech activity does not identify a speaker or transcribe.
* Missing analysed-media intervals and analysis/reference failures.

Duration thresholds are versioned in `app/monitoring/rules.py` and copied into each
run. They are preliminary, not validated misconduct criteria. Model confidence is
not a probability of cheating. Frames can miss brief/out-of-frame events; background
photos, ordinary movements, environmental voices, and accommodations require context.
Sunglasses detection, exact gaze, speaker separation/identity, and strong anti-replay
are explicitly unsupported in the capabilities record. They require separately
validated models and, where needed, further approval.

Samples hold model metrics and boxes, never images/embeddings. Events reference
part-relative media offsets and five seconds of context either side; wall-clock
alignment to the exam is approximate because recording begins around the start
request. Coverage-gap offsets use exam-session start and have no fabricated evidence
link. Existing protected recording playback provides the footage. An `analysed` job
means received media was processed, not that all exam time or identity was observable.
Coverage reports distinguish analysed time from usable-face/identity-check time.
The reviewer UI should refresh events from the start to see extensions to existing
events; `after_id` paginates IDs and is not an update/change feed.

Reference images are deleted after the configured recording retention period.
Deleting media removes its per-frame samples and playback links, while event metadata
can remain with unavailable evidence. No historical attempt is silently enrolled in
monitoring. The candidate notices and consent version describe this processing.

Validation includes real-model inference, encrypted WebM and MP4 ingestion, speech
and silence probes, policy-gated prohibited-item flags, additional-person default, event merging,
restart replay, ownership restrictions, coverage gaps, and unchanged exam outcomes.
Real SEB webcam sessions, accuracy calibration, and deployment load tests are still required before production use.


## Human review

Open **Review cases**, search or filter the assessments, select an exam, and open a
candidate card. Candidate filters include review state, attempt state, observations,
and monitoring attention.
Reports have Overview, Results, Integrity report, and Connectivity & interruptions
tabs plus a decision panel.
Results & reports provides an assessment-wide score/outcome table with report links.

The owner can review submitted, interrupted, and automatically disqualified attempts. Live attempts
can be inspected and observations assessed, but dispositions require the attempt to end.
The report presents exam time used, schedule and actual times, original multiple-choice
marking, written grading, final score, identity outcome, video/audio/identity coverage,
analysis status, timestamped observations, and complete disposition/finding histories.

The protected session player opens the full recording, with an in-player timeline,
ten-second skip controls, and a playback-speed button cycling from 0.75x to 2x in
0.25x steps. An observation opens its video in the observation card,
starts at the flagged interval, highlights that interval on the full-session timeline,
and loops it until the reviewer seeks elsewhere or turns looping off. Both players
support Space/K for play or pause, Left/Right for five-second seeking, J/L for
ten-second seeking, M for mute, Up/Down for volume, Home/End for the start or end,
and F for fullscreen while the player is focused. The verified
reference photo can be opened separately when retained. Missing or expired footage
is shown as unavailable. Media and report access are logged. Reference photos and
answers never appear in the public API or another assessment owner's report.

All questions currently have one mark. Written answers accept 0..1 marks (two decimal
places) and a grading explanation. Unanswered written questions receive zero. All
answered written questions must be graded before Keep score or Change score. Original
automatic marks remain available. A manual total adjustment is separate from question
grades, and must be within 0..question count. Changing only a grading explanation does
not silently reset an existing total adjustment.

Each observation can be assessed as Supports a rule violation, Dismissed, or Inconclusive,
with a required explanation. Finding revisions preserve the earlier finding and an
observation snapshot. Findings never automatically make a final disposition.

Connectivity incidents group a server-recorded interruption start with its recovery or
terminal event. The tab shows the observed trigger, the end state, face-check results,
and estimated nearby video/audio gaps after evidence processing. A missing heartbeat
has an unknown cause; no deliberate exit is inferred. The reviewer may record
Explained, Unresolved, or Needs attention with a reason. These findings retain the
reviewer identity and previous revisions, share the review revision lock, and cannot
be changed after publication. The auditor sees the findings read-only, and a later
finding invalidates an earlier audit mark until it is audited again.

Every Keep score, Change score, or Disqualify decision requires a reason and a separate
confirmation. The server attaches the authenticated reviewer's ID, username snapshot,
server timestamp, previous outcome/score, grading, and referenced observations. Client
payloads cannot supply reviewer information. Decisions append immutable history rows;
a later decision can revise a HUMAN disposition. Disqualification preserves earned marks
and does not turn them into zero. Candidate History reflects the latest published final outcome.

**System disqualifications cannot be overturned**, including by
requesting Keep score or Change score directly through the API. The original attempt
record is never reopened or rewritten by a review. Concurrent/stale edits are rejected
using a shared session review revision. UUID request keys make decision retries idempotent.
Only the personnel account that owns the assessment can read or write its reviews.

Review endpoints (all require the owning employer-personnel session):

* GET `/api/employer/assessments/{id}/reviews`: candidate cards and final outcomes.
* GET `/api/employer/assessments/{id}/invitations/{invitation_id}/review`: session report.
* POST `.../review/decisions`: reasoned final disposition.
* POST `.../review/events/{event_key}/findings`: reasoned observation finding.
* POST `.../review/interruptions/{incident_id}/findings`: reasoned interruption finding.
* GET `.../review/reference`: retained verified camera image; access audited.

The UI loads all monitoring pages, and an explicit report reload refreshes ongoing
observations. It warns before discarding unsaved grades/reasons and rejects a stale
review rather than overwrite another tab. Processing or missing evidence remains
visible. Final decisions wait for recording upload and analysis to finish.

Run `python -m unittest discover -s backend/tests -p "test_*.py"` for API/database
regressions. With the local app on port 8000, `python backend/tests/browser_review_probe.py`
uses installed Chrome and disposable database fixtures to exercise the actual UI,
recording/reference playback, findings, grading, decision revision, and 390px layout.
It removes the temporary page, accounts, assessments, and recording files afterward.


## Connectivity-aware exam sessions

The exam keeps its original server deadline. During a connection interruption, candidates
can edit the current answer and recording continues locally; question navigation requires
an acknowledged save and confirmed server contact. Browser offline events immediately pause navigation while a server check runs; failed exam or upload requests also block navigation until a heartbeat succeeds. Switching off a hotspot's external internet does not disconnect a server running on the candidate's own 127.0.0.1. Local recovery tests must block the exam server requests or stop access to that server. Answers and recording chunks are encrypted in IndexedDB and retried
after reconnection. Only answers received by the server before the deadline are accepted.
Timeout submits the saved progress.

Recovery permits up to three minutes per interruption and ten minutes total. Exceeding
a limit before the deadline ends the attempt as Interrupted for human review, not automatic
disqualification. Camera/microphone or local-storage failures stop answering and use the
same recovery allowance. Upload delay alone does not stop capture. Confirmed Exit SEB
during an unfinished attempt still disqualifies it; heartbeat silence or a new sign-in
does not prove a deliberate exit. Native termination without an application exit request
is handled as an interruption.

A new exam page must repeat connection/storage readiness and match a fresh camera image
to the original verified reference before resuming the same attempt. It cannot reset the
timer or recovery allowance. Starting requires a 128 KiB upload within ten seconds.

Recording uploads can finish for fifteen minutes after the attempt ends. Missing chunks
remain incomplete after that grace period. The candidate sees remaining-upload byte progress and retry status; normal Exit exam stays hidden until the server confirms collection ended. Upload errors retain the local queue and show Retry. After the grace period, the server records missing collection as incomplete before enabling normal exit. This application gate cannot prevent SEB's native emergency quit, a forced process termination, or power loss. Final dispositions, audits, and publication wait
until recording collection and queued analysis finish. Recovery events appear in reports.
Local queued media is limited to 64 MiB; expired encrypted payloads are cleared when the
page next opens, and acknowledged media is removed immediately. Browser data deletion or
loss of the device can destroy unsent evidence.

Run `python -m unittest discover -s backend/tests -p test_connectivity_integration.py`
for database/API recovery checks. With the app on port 8000, run
`python backend/tests/browser_connectivity_probe.py` for Chrome offline/reload/live-face
recovery, draft synchronization, and recording cleanup using disposable fixtures.
Validation on installed Windows and macOS SEB clients is still required.


Candidate readiness is recorded before a new attempt. Instructions/rules, room/device
readiness and the interruption/exit policy require explicit acknowledgements. Checks
cover Windows/macOS, secure context, usable camera/microphone tracks, MediaRecorder,
Web Audio, encrypted IndexedDB, the server upload probe, identity and monitoring consent.
The server requires a passing record from the same invitation, account, client and exam
access, no older than five minutes, matching the current instruction digest. Device
feature and platform claims are client-reported; they are not OS attestation.

SEB configuration proofs use its documented JavaScript API or native request header.
Version information is recorded when the bridge provides it. Visibility, focus,
fullscreen and media-track transitions are browser observations, queued in encrypted
local storage (up to 200 queued events), retried with idempotent identifiers, and shown
in Connectivity & interruptions, case history and PDF reports. Server receipt time is
separate from the device clock. Capture on page closure is best effort. These events
never automatically disqualify an attempt. Native crash, forced quit and prohibited-app
telemetry require a separate native/SEB Server integration; the public page API does not
provide those callbacks. Installed Windows/macOS client validation remains in todo.
For an existing database, run `python backend/migrate_readiness.py` from the project root.


Company administrators manage retention in Company settings. Identity images and
recordings default to 30 days; case records and audit metadata have no automatic expiry
unless a period is set. Each newly created assessment freezes its company policy.
Existing assessments retain their saved periods, and legacy rows are migrated with
no case/audit expiry. Policy changes require a reason, use revision checks, and record
the signed-in administrator. Images expire after capture, recordings after the attempt
ends, and case/audit periods run from the assessment closing time. Case retention must
cover image/recording periods; audit retention must cover the case period.

Automatic cleanup runs approximately once a minute. Case expiry removes the assessment
and its assignments, answers, scores, findings, notes, identity evidence and recordings.
Minimal action types, account references and timestamps are retained separately until
the saved audit deadline, then removed. Administrators can view that metadata under
Company settings; it contains no answer, reason, score, image, email, name or IP address.
A failed filesystem deletion leaves the case for a later cleanup retry; expired cases
are already unavailable to normal access and exports. Downloaded copies and backups
are outside this cleanup. Account profiles and platform authentication/security logs
are outside company assessment retention. Account profiles remain until a separate account lifecycle policy applies. Server operators can set `SECURITY_AUDIT_RETENTION_DAYS` for new authentication and staff-access audit events; blank means no automatic expiry. Existing log expiry dates remain unchanged.
For an existing database run `python backend/migrate_retention.py` from the project root.
`IDENTITY_RETENTION_DAYS` and `RECORDING_RETENTION_DAYS` set server defaults for new
policies, not retroactive changes to frozen assessment policies or saved expiry dates.


## Fayda demo authentication with eSignet

This optional environment uses **fictional identities**, not Fayda's live registry.
AssessBridge remains FastAPI, vanilla HTML/CSS/JS and MySQL. The separate identity
provider runs MOSIP eSignet 2.0.0, its supplied React interface, the mock identity
service 0.14.0 and PostgreSQL 16 through Docker Compose.

### Start locally

1. Complete the normal AssessBridge setup, including MySQL, `.env`, the approved
   identity models, and `pip install -r backend/requirements.txt`.
2. Install and start Docker Desktop (Windows/macOS), or Docker Engine with Compose
   (Linux). On this development computer the setup command also supports the
   Docker Engine installed in the Ubuntu WSL distribution.
3. Run `.venv\Scripts\python.exe backend/init_db.py` to apply migrations.
4. Run `.venv\Scripts\python.exe backend/setup_esignet.py`.
5. Restart FastAPI, then refresh the employer dashboard.

On Linux/macOS, use `.venv/bin/python` in place of `.venv\Scripts\python.exe`.
The first start downloads the images and can take several minutes. The script
creates private configuration under ignored `var/esignet/`, adds the demo settings
to your local `.env`, initializes the separate PostgreSQL database, seeds the
identities, registers AssessBridge's OIDC client, and checks provider discovery.
It can be run again safely without deleting existing data or regenerating keys.
Never commit `var/esignet/`, `.env`, private keys or database volumes.

Use `python backend/setup_esignet.py --stop` to stop the demo containers without
deleting their data. If using the WSL fallback, a hidden Linux session keeps
Docker available while Windows is running; Docker Desktop manages this itself.

### Test identities

| Name to use in the invitation | Test identity number | Demo OTP |
|---|---|---|
| Daniel Bekele | 900000000001 | 111111 |
| Samuel Mensah | 900000000002 | 111111 |
| Grace Njeri | 900000000003 | 111111 |

1. Register a candidate account using an email you can access, and verify that email.
2. As employer personnel, create an assessment. Under **Rules & identity**, select
   **Fayda demo authentication**.
3. In the roster, enter one of the names above and the candidate account's actual
   email. Send the invitation and wait for the exam's scheduled access window.
4. Open the invitation's SEB configuration, sign in to the candidate account and
   enter the exam-only credentials. Existing SEB requirements remain in force.
5. Accept consent, check the camera and microphone, and select **Authenticate test
   identity**. In eSignet enter the corresponding test identity number, and
   use **111111** when asked for the OTP. Approve sharing the name.
6. Back in AssessBridge, reconnect the camera if asked, capture your own session
   photo, complete readiness acknowledgements and start the exam.

No real SMS or email OTP is sent by the mock provider. The candidate invitation
email is still sent through AssessBridge's configured SMTP account. The fictional
contact details stored in the mock registry do not bind or replace the candidate's
registered email. Different testers can reuse a demo identity; their accounts,
exam invitations, attempts, recordings and results remain separate.

This mode does not run card OCR or compare a fictional portrait with the tester.
It checks the authenticated test name against the invitation, then stores an
encrypted camera photo for continuity monitoring and recovery face checks. Review,
audit and exported reports identify the result as **Demo identity authenticated**;
no card/face match score is invented. The initial session photo is not proof of
liveness or official identity.

### Configuration and deployment

`ESIGNET_DEMO_ENABLED=false` hides the exam option and blocks the demo endpoints.
Keep it disabled on an installation conducting real examinations. Existing card
verification options do not use the mock provider.

Local defaults are AssessBridge `http://127.0.0.1:8000` and the authentication page
`http://127.0.0.1:3000`. Use one consistent hostname. Database and mock service
ports are not published, and the identity page is bound to loopback only. For a
public **demonstration** deployment, configure HTTPS origins, a reverse proxy to
the identity UI, and run the setup script with `--issuer https://identity.example`
and `--app-url https://assessbridge.example`. The script registers that exact
callback. Existing exam configurations retain their original settings; create new
demo invitations after changing the identity-provider origin.

SEB configurations for new demo invitations allow the configured identity origin.
Other exam configurations keep their existing navigation rules. Validate the
redirect and camera return on your installed Windows/macOS SEB client before
using that deployment.

Troubleshooting: if setup fails, inspect container status and logs rather than
continuing with a partially started provider. From the project root, use
`docker compose --env-file var/esignet/runtime.env -f infra/esignet/compose.yaml ps`
and the same command ending in `logs --tail 50`. With the Ubuntu WSL fallback,
run Docker commands inside that distribution using Linux paths. For localhost
connection failures, confirm Docker/WSL is still running and rerun setup. Do not
remove volumes to troubleshoot; they contain provider signing keys and identities.

The upstream database schema and its MPL-2.0 license are included under
`infra/esignet/upstream/`, pinned to upstream commit
`db528423dac7898ad546cb7b2f5f867a949676a7`. Images use explicit release tags.

### Verification

The opt-in live test uses disposable AssessBridge accounts and exams, and checks
all three seeded identities through OTP, consent and signed OIDC return. Start
the demo services first, then on PowerShell run:

```powershell
$env:PYTHONPATH = "backend;backend/tests"
$env:RUN_ESIGNET_LIVE = "1"
.venv\Scripts\python.exe -m unittest backend.tests.test_esignet_live -v
```

The regular `test_esignet_integration` and `test_esignet_setup` suites use mocked
provider responses or isolated temporary setup files; they do not require Docker
or send email. Installed-client SEB validation is a separate manual check.
