<picture>
  <source media="(prefers-color-scheme: dark)" srcset="frontend/assets/branding/logo-white.svg">
  <img src="frontend/assets/branding/logo-blue.svg" alt="AssessBridge" width="320">
</picture>

# AssessBridge

1. **Live application:** [Add link](LIVE_APPLICATION_URL)
2. **Demo video:** [Add link](DEMO_VIDEO_URL)

AssessBridge is a company-scoped assessment platform for creating examinations, monitoring candidate sessions, reviewing evidence, auditing outcomes and publishing results.

The application combines scheduled assessments, secure exam entry, audiovisual recording and automated integrity observations with a human review and audit process. Identity authentication uses an eSignet simulation populated with fictional identities. A live session photo provides the reference for identity-continuity monitoring.

## Contents

- [Features](#features)
- [Roles](#roles)
- [Technology](#technology)
- [Monitoring reference](#monitoring-reference)
- [Local setup](#local-setup)
- [Fictional test identities](#fictional-test-identities)
- [Running the tests](#running-the-tests)
- [Starting and stopping](#starting-and-stopping-an-existing-installation)
- [Configuration](#configuration-notes)
- [Troubleshooting](#troubleshooting)
- [Repository layout](#repository-layout)

## Features

- **Company administration:** company registration, staff invitations, administrator access management and configurable data-retention policies.
- **Assessment authoring:** multiple-choice and written questions, answer keys, prohibited-item rules, candidate rosters, scheduling and calendar invitations.
- **Candidate sessions:** verified email accounts, separate exam credentials, Safe Exam Browser integration, consent, device checks, live photos, autosave, a server-controlled timer and automatic submission.
- **Connectivity recovery:** local answer persistence, navigation restrictions during disconnection, bounded recovery and a fresh face check before resuming an interrupted session.
- **Session monitoring:** identity continuity, additional people, mobile phones, books, tablets, calculators, head orientation, speech activity and recording-quality observations.
- **Review and grading:** automatic multiple-choice grading, manual written-response grading, evidence playback, recorded findings and reasoned review decisions.
- **Audit and publication:** audits of closed assessments, publication controls, publication history, result CSV exports and candidate audit-report PDF exports.

Automated monitoring observations are evidence for human review; they do not automatically disqualify candidates. Exam controls separately enforce session rules, including explicitly leaving an active, unsubmitted exam.

## Roles

| Role | Responsibilities |
| --- | --- |
| Company administrator | Register the company, invite and manage staff, and configure company retention settings. |
| Employer personnel / reviewer | Create assessments, invite candidates, grade written responses and review session evidence. |
| Employer representative / auditor | Audit reviewed attempts from closed assessments and approve publication when all candidates meet the publication requirements. |
| Candidate | Register and verify an email address, take invited assessments and view published results. |

Staff access is scoped to the company that owns the assessment. Administrator, reviewer and auditor accounts have separate responsibilities.

## Technology

| Component | Implementation |
| --- | --- |
| Application frontend | HTML, CSS and vanilla JavaScript |
| Application backend | Python and FastAPI |
| Application database | MySQL |
| Identity simulation | eSignet and a mock identity service, with their supplied authentication UI and a separate PostgreSQL database |
| Identity-service runtime | Docker Compose |
| Model inference | OpenCV and ONNX Runtime |
| Audio/video decoding | FFmpeg |
| Candidate exam environment | Safe Exam Browser; an optional browser-only development mode is available |

FastAPI serves both the frontend and API. A separate, automatically supervised Python worker analyses uploaded recordings. Docker Compose runs the identity services; it does not start the main application or its MySQL server. PostgreSQL is supplied inside that identity stack and does not need a separate host installation.

## Monitoring reference

AssessBridge records automated observations for human review. An observation is not a finding of misconduct and does not automatically disqualify a candidate. Reviewers examine the recording, monitoring coverage and surrounding context before recording a finding or final decision. Connectivity interruptions and browser/device events are recorded separately from the monitoring flags below.

The current monitoring policy is **`monitoring-2.4`**. Each monitoring run stores its policy snapshot; existing runs can therefore retain earlier thresholds. Policy definitions, inference logic and event aggregation are maintained in [rules.py](backend/app/monitoring/rules.py), [engine.py](backend/app/monitoring/engine.py), [worker.py](backend/app/monitoring/worker.py) and [repository.py](backend/app/monitoring/repository.py).

### Sampling and event thresholds

Video analysis samples **two frames per second**, with each check representing approximately **0.5 seconds** of recorded media. Audio analysis uses blocks of approximately **1.024 seconds**. Duration thresholds refer to qualifying evidence in the recording, not processing time. Audio events can cross a threshold at the next block boundary.

Most duration-based events require consecutive positive checks. A negative check or a processing gap breaks the sequence. Phone, book and framing observations use the rolling-window rules specified below. Aggregation is performed separately for each recording part; sequences do not carry across recording-part boundaries.

### Head orientation

| Observation | Event identifier | Trigger |
| --- | --- | --- |
| Sustained head turn | `head_turned` | Sideways head-orientation change relative to the pre-exam session photo for **5 consecutive seconds**. |
| Downward head orientation | `head_down` | Downward head-orientation change relative to the session photo for **8 consecutive seconds**. |
| Repeated head-orientation changes | `repeated_look_away` | At least **three completed episodes** of sideways or downward orientation, each lasting **2 seconds or more**, retained in a rolling **60-second window**. Overlapping episodes are merged. |

Orientation is a coarse estimate derived from facial landmarks. The sideways threshold is an absolute landmark-ratio change greater than `0.22`; the downward threshold is a ratio increase greater than `0.20`. These values are not angles in degrees. A usable face and reference pose are required. The system does not track eye gaze or determine what a candidate is looking at.

### Identity and additional people

| Observation | Event identifier | Trigger |
| --- | --- | --- |
| Possible identity mismatch | `identity_mismatch` | Face similarity to the session-photo reference remains below the **`0.40` cosine-similarity threshold** for **5 consecutive seconds** of qualifying comparisons. |
| Face absent | `face_absent` | No qualifying face is detected for **5 consecutive seconds**. This can occur even when part of a person remains visible. |
| Face could not be checked | `face_unusable` | A detected face remains too small, insufficiently sharp or affected by an obscured image for **8 consecutive seconds**. |
| Additional person detected | `additional_person` | More than one qualifying face or person is detected for **2 consecutive seconds**. This check is enabled for every assessment. |

Identity continuity compares the dominant visible face with the **pre-exam session photo**, not an ID-card portrait. Face comparison requires sufficient image quality and a suitable orientation. An unusable or strongly angled face is not automatically treated as an identity mismatch. The similarity threshold is not a percentage probability of identity or misconduct.

At the analysed video resolution, detected faces smaller than 25 pixels on either side are excluded. A face is usable for orientation and identity checks when its width and height are at least 75 pixels, its Laplacian sharpness measure is at least `12`, and the image is not classified as obscured.

### Prohibited items

These observations are enabled only for items selected as prohibited in the assessment configuration.

| Observation | Event identifier | Trigger |
| --- | --- | --- |
| Phone visible | `mobile_phone` | At least **1.5 seconds of detected evidence within a rolling 3-second window**, tolerating up to **0.5 seconds** of missed detections. |
| Book visible | `book` | At least **1.5 seconds of detected evidence within a rolling 3-second window**, tolerating up to **0.5 seconds** of missed detections. |
| Tablet visible | `tablet` | **1.5 consecutive seconds** of detections. A missed check resets the sequence. |
| Calculator visible | `calculator` | **1.5 consecutive seconds** of detections. A missed check resets the sequence. |

Missed intervals do not contribute to the required evidence duration. For phones and books, three positive half-second checks can qualify despite one intervening negative check. Isolated detections do not qualify.

YOLOX detects people, phones and books with a minimum detection score of `0.45`. RT-DETR, trained on Objects365, detects tablets and calculators with a minimum score of `0.30`. Model scores measure detection strength; they do not establish a rule violation. Object visibility alone does not guarantee an observation: the detector must recognise the object sufficiently often to satisfy the aggregation rule.

### Camera image and framing

| Observation | Event identifier | Trigger |
| --- | --- | --- |
| Camera image obscured | `camera_obscured` | Extremely dark, extremely bright or nearly uniform video for **5 consecutive seconds**. |
| Suspected frozen video | `suspected_frozen_video` | Successive analysed images remain almost identical for **15 consecutive seconds**. |
| Framing changed | `framing_changed` | **5 seconds of changed-position evidence within a rolling 7-second window**, tolerating up to **1 second** without qualifying evidence. Returning to normal framing resets the sequence. |

An obscured-image signal is generated when mean grayscale brightness is below `15` or above `248`, or grayscale standard deviation is below `4`. Frozen-video detection compares successive 64 × 36 grayscale images and signals a mean absolute difference below `0.20`. A still scene can resemble frozen footage; the observation requires contextual review.

Framing compares the face centre with its initial usable position in that recording part. A Euclidean displacement greater than `0.25` in normalised image coordinates produces a changed-position signal. This identifies a composition change; it does not distinguish camera movement from candidate movement.

### Audio

| Observation | Event identifier | Trigger |
| --- | --- | --- |
| Speech activity | `speech` | Speech-positive audio blocks continue for at least **3 seconds**. A block qualifies when at least **30%** of its short segments have a speech probability of **`0.50` or higher**. |
| Repeated speech activity | `repeated_speech` | At least **three completed speech episodes**, each lasting **2 seconds or more**, retained in a rolling **60-second window**. |
| Audio clipping | `audio_clipping` | Clipping-positive blocks continue for at least **3 seconds**. A block qualifies when at least **2%** of its samples have an absolute amplitude of **`0.995` or higher** on the normalised audio scale. |
| Digital audio silence | `digital_audio_silence` | Essentially zero-valued audio continues for at least **10 seconds**. A block qualifies when its maximum absolute sample amplitude is below **`1e-7`**. |

Silero VAD detects speech activity. It does not identify speakers, transcribe speech or determine whether speech concerns the assessment. Digital silence refers to the recorded signal and is distinct from an ordinary quiet room. Clipping indicates possible recording distortion, not misconduct.

### Evidence availability and analysis diagnostics

| Observation | Event identifier | Trigger |
| --- | --- | --- |
| Identity reference unavailable | `identity_reference_unavailable` | A usable reference face cannot be extracted from the stored session photo. Identity continuity cannot be checked for the affected recording part. |
| Video analysis unavailable | `video_analysis_unavailable` | Video processing fails, including decoding or inference errors. |
| Audio analysis unavailable | `audio_analysis_unavailable` | Audio processing fails, including decoding or inference errors. |
| Video coverage gap | `video_coverage_gap` | After processing, an interval longer than **2.5 seconds** has no analysed video coverage. |
| Audio coverage gap | `audio_coverage_gap` | After processing, an interval longer than **2.5 seconds** has no analysed audio coverage. |

Diagnostics describe limitations in the available evidence. They do not establish deliberate interference. Coverage gaps are recalculated after processing retries, so an earlier gap can disappear when analysis fills the missing interval. A completed processing job does not, by itself, establish uninterrupted monitoring coverage.

## Local setup

Complete the steps below in order. Commands run from the repository root unless another location is stated. Each command is a single line and can be copied independently.

The default local addresses are:

| Service | Address |
| --- | --- |
| AssessBridge | `http://127.0.0.1:8000` |
| eSignet simulation | `http://127.0.0.1:3000` |
| MySQL | `127.0.0.1:3306` |

This setup runs the application and the candidate exam on the same computer. An invitation containing `127.0.0.1` points to the computer opening that invitation. Access from other computers requires reachable HTTPS addresses and corresponding application, identity-provider and SEB configuration.

### 1. Install the prerequisites

| Requirement | Installation and configuration |
| --- | --- |
| Git | Install [Git](https://git-scm.com/downloads) and make it available in the terminal. |
| Python | Install a **64-bit Python 3.11 or newer** interpreter from [python.org](https://www.python.org/downloads/). The validated Windows setup uses Python 3.14. Enable the Windows Python launcher when installing on Windows. |
| MySQL Server | Install and start **MySQL 8.0**. The validated setup uses MySQL 8.0.46. Configure a standalone server on port `3306` and retain its administrative login. See the [MySQL installation guide](https://dev.mysql.com/doc/refman/8.0/en/installing.html) and [Windows installer](https://dev.mysql.com/downloads/installer/). |
| Docker and Compose | Install [Docker Desktop on Windows](https://docs.docker.com/desktop/setup/install/windows-install/) or [macOS](https://docs.docker.com/desktop/setup/install/mac-install/), or [Docker Engine with the Compose plugin](https://docs.docker.com/compose/install/) on Linux. Start the engine. On Windows, use Linux containers with the WSL 2 backend. |
| Browser | Use a current Chrome or Chromium browser for account setup and the automated browser tests. |
| Safe Exam Browser | Install [SEB for Windows](https://www.safeexambrowser.org/download/) on the candidate computer for the standard exam workflow. The validated native exam environment is Windows SEB 3.10.2. The macOS SEB workflow has not been validated. |
| Camera and microphone | Both are required for the candidate workflow. |
| Email delivery | Have an SMTP account that can send verification, staff-invitation and assessment-invitation emails. Gmail configuration is covered in step 6. |

Allocate sufficient memory and disk space for the identity containers, Python packages, model weights and recordings. The local models run on the CPU; a GPU is not required. Initial setup needs internet access to download dependencies, container images and model assets.

Check Git, MySQL and Docker from the terminal:

```sh
git --version
mysql --version
docker version
docker compose version
```

`docker version` must show a responding server. On Linux, the current user must be able to run Docker without an interactive `sudo` prompt. If `mysql` is not found, add the MySQL `bin` directory to `PATH` or use the MySQL Command Line Client supplied with the installation.

The setup script also supports Docker installed inside a WSL distribution named `Ubuntu` when Docker is unavailable directly on Windows. In that configuration, verify access with:

```sh
wsl -d Ubuntu -u root -- docker version
wsl -d Ubuntu -u root -- docker compose version
```

### 2. Clone the repository

```sh
git clone --branch main https://github.com/NatnaelAyele/AssessBridge.git
cd AssessBridge
```

The repository's `.gitattributes` preserves Linux line endings for shell scripts on Windows.

### 3. Create the Python environment and install dependencies

**Windows PowerShell:**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, allow local scripts for the current terminal process, then retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

**Windows Git Bash:**

```sh
py -3 -m venv .venv
source .venv/Scripts/activate
```

**Linux / macOS:**

```sh
python3 -m venv .venv
source .venv/bin/activate
```

On Linux, install the distribution's `python3-venv` package if the `venv` module is unavailable. See the [Python virtual-environment documentation](https://docs.python.org/3.14/library/venv.html) for platform details.

With the environment active, install the application packages:

```sh
python --version
python -m pip install -r backend/requirements.txt
python -m pip check
```

Confirm that `python --version` reports 3.11 or newer. A successful dependency check reports `No broken requirements found`.

### 4. Create the local MySQL account

Open an administrative MySQL session:

```sh
mysql -h 127.0.0.1 -P 3306 -u root -p
```

On Linux installations using socket authentication for the administrative account, open it with `sudo mysql` instead.

Run the following SQL. Replace `choose-a-strong-local-password` with a password chosen for this installation and retain it for step 5:

```sql
CREATE USER 'assessbridge_local'@'127.0.0.1' IDENTIFIED BY 'choose-a-strong-local-password';
GRANT ALL PRIVILEGES ON assessbridge.* TO 'assessbridge_local'@'127.0.0.1';
GRANT ALL PRIVILEGES ON `assessbridge\_test\_%`.* TO 'assessbridge_local'@'127.0.0.1';
EXIT;
```

The first grant permits initialization and use of the application database. The second permits disposable databases named `assessbridge_test_...` for integration tests. These grants target the local MySQL 8.0 setup; see [MySQL database-level grants](https://dev.mysql.com/doc/refman/8.0/en/grant.html) for other server configurations.

Verify the new account using the password just selected:

```sh
mysql -h 127.0.0.1 -P 3306 -u assessbridge_local -p
```

```sql
SELECT VERSION();
EXIT;
```

### 5. Create the application configuration

Copy the example configuration once.

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

**Git Bash / Linux / macOS:**

```sh
cp .env.example .env
```

Open `.env` in a text editor. Set the database connection to the account created in step 4:

```dotenv
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=assessbridge
DB_USER=assessbridge_local
DB_PASSWORD='your-local-database-password'
```

Generate the authentication secret:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the output into `JWT_SECRET` in `.env`, replacing the placeholder.

Generate the recording-encryption key separately:

```sh
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Copy that output into `RECORDING_KEY`. Keep both keys stable for this installation. Existing encrypted recordings and identity images depend on the recording key.

Retain these local settings:

```dotenv
COOKIE_SECURE=false
EXAM_ENTRY_URL=http://127.0.0.1:8000/exam-entry
SEB_BASE_URL=http://127.0.0.1:8000
SEB_REQUIRED=true
RECORDINGS_DIR=var/recordings
MONITORING_AUTO_START=true
MONITORING_CONCURRENCY=2
```

Use `.env` for local values. `.env.example` is the committed template and is not the active configuration. `.env`, private keys, model weights and generated recordings are excluded from Git.

### 6. Configure email delivery

Email delivery is required to complete company activation, candidate email verification, staff invitations and exam invitations.

For Gmail:

1. Sign in to the Google account that will send platform emails.
2. Enable **2-Step Verification** in the account's security settings.
3. Create an **App Password** following [Google's instructions](https://support.google.com/accounts/answer/185833).
4. Enter the sender address and generated app password in `.env`:

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=your-sender@gmail.com
SMTP_PASSWORD='your-google-app-password'
SMTP_SENDER=your-sender@gmail.com
```

Use the app password without its display spaces. `SMTP_PASSWORD` is the app password, not the normal Google sign-in password. Some managed Google accounts do not permit app passwords; use an SMTP account that supports the authentication configured here.

For another provider, supply its SMTP host, login, authorized sender and either `starttls` or `ssl`. A typical SSL connection uses port `465` with `SMTP_SECURITY=ssl`.

Company activation in step 11 provides the first end-to-end email-delivery check. Check the recipient's spam folder as well as the inbox.

### 7. Initialize the application database

```sh
python backend/init_db.py
```

The initializer creates `assessbridge` and applies the schema and included migrations. Successful output includes:

```text
Database schema is ready.
Demo identity authentication schema is ready.
eSignet-only identity workflow ready.
```

The final message may include counts of updated records. The initializer does not create company, staff or candidate login accounts; those are registered through the application.

### 8. Download the models and install FFmpeg

```sh
python backend/fetch_identity_models.py
python backend/fetch_monitoring_models.py
```

Wait for both commands to finish successfully. The scripts verify model checksums. The monitoring script downloads the ready-to-use tablet/calculator model from the project's [model release](https://github.com/NatnaelAyele/AssessBridge/releases/tag/monitoring-models-v1), validates its class mapping and runs an inference check.

Normal setup does not require the model-export environment or `requirements-model-export.txt`. Model provenance and optional rebuilding instructions are in [MONITORING_MODELS.md](backend/models/MONITORING_MODELS.md).

**Windows:** the monitoring download script also installs a verified FFmpeg binary at `var/ffmpeg/ffmpeg.exe`. Leave `FFMPEG_CMD` blank to use automatic discovery.

**Linux / macOS:** install [FFmpeg](https://ffmpeg.org/download.html) separately. For Ubuntu/Debian, use `sudo apt install ffmpeg`; with Homebrew on macOS, use `brew install ffmpeg`. Verify it with:

```sh
ffmpeg -version
```

If FFmpeg is installed outside `PATH`, set `FFMPEG_CMD` in `.env` to its absolute executable path.

### 9. Prepare and start the eSignet simulation

Ensure Docker is running, then execute:

```sh
python backend/setup_esignet.py --issuer http://127.0.0.1:3000 --app-url http://127.0.0.1:8000
```

This command generates local signing keys and runtime configuration, starts the identity services and their PostgreSQL database, registers the application client and loads the fictional identities. It also updates the `ESIGNET_*` settings in `.env`.

The first run downloads container images and can take several minutes. Wait for:

```text
Demo services are ready. Restart FastAPI to load its demo configuration.
```

Verify discovery from the active Python environment:

```sh
python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:3000/.well-known/openid-configuration'))['issuer'])"
```

Expected output is `http://127.0.0.1:3000`. Begin authentication from AssessBridge during exam entry; the identity provider needs an application-initiated authentication request.

The stack's PostgreSQL credentials and signing material are generated locally under `var/esignet/` and in Docker volumes. Do not substitute application MySQL credentials for them.

### 10. Generate SEB configuration and start AssessBridge

Generate the local generic SEB configuration after setting up eSignet:

```sh
python backend/make_seb_config.py --base-url http://127.0.0.1:8000
```

The command writes `backend/seb/AssessBridge.seb` and updates the SEB keys and passwords in `.env`. New exam invitations receive their own configuration, passwords and exam credentials; candidates should use the configuration associated with their invitation.

Start FastAPI:

```sh
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Keep this terminal open. Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in the browser. FastAPI also starts and supervises the monitoring worker when `MONITORING_AUTO_START=true`; a second manually started worker is not needed.

In another terminal, activate the same Python environment and check application liveness:

```sh
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/live').read().decode())"
```

Expected output is `{"status":"ok"}`. Confirm worker readiness in `var/monitoring-worker.log`; successful startup includes `Monitoring worker ready`. Liveness alone does not confirm that the database, models or worker are ready.

API documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### 11. Register a company and its staff

Use distinct, accessible email addresses for the administrator, reviewer, auditor and candidate accounts. An email address cannot be reused for separate accounts.

For local testing, email aliases can be used if the mail provider supports them. Each alias must be a distinct address in AssessBridge and must receive verification and invitation messages in an inbox accessible to the tester.

1. Open the sign-in page and select **Register company**.
2. Enter the company name, administrator name, email address and password. Account passwords require at least 12 characters.
3. Open the company-activation email in a regular browser and activate the company.
4. Sign in with the administrator's email and password.
5. From the company workspace, invite an **Employer personnel** account and an **Auditor** account. Additional administrators can also be invited.
6. Open each staff invitation and set that account's password.
7. Sign in with each account to access its assigned dashboard.

There are no bundled administrator, reviewer or auditor passwords. Staff accounts need company membership; use the company invitation workflow to create them.

### 12. Create an assessment and invite a candidate

Sign in as employer personnel and complete the assessment editor:

1. **Basics and duration:** enter the title, instructions, optional exam code and duration.
2. **Questions and keys:** add multiple-choice or written questions and the corresponding answer keys.
3. **Rules and identity:** choose prohibited items and retain the eSignet identity method.
4. **Candidate roster:** enter an accessible candidate email and one fictional full name from the identity table below.
5. **Schedule:** choose a future opening time and a closing time that allows the full exam duration.
6. **Final review:** confirm the details and send invitations.

For an initial test, a short assessment with one multiple-choice question and one written question is sufficient. Allow time to receive the invitation and complete account verification before the exam window closes.

The invitation includes an exam link, calendar invitation, exam-only username/password and the SEB configuration and exit codes. A failed delivery leaves the assessment editable in the editor; correct the delivery issue and retry.

### 13. Register the candidate and take the exam

Before opening SEB:

1. Open the invitation link in a regular browser.
2. Register a candidate account using **the exact email address that received the invitation**. An existing verified candidate account can be used instead.
3. Open the candidate-verification email and verify the address.
4. Return to the invitation page and select **Open in Safe Exam Browser**. If the browser does not launch SEB, download and open that invitation's `.seb` file.
5. Enter the configuration password from the invitation when SEB requests it.

Inside the exam environment:

1. Sign in to the candidate's platform account.
2. Enter the separate exam-only username and password from the invitation.
3. Open the assessment when its access window is open and accept the consent notice.
4. Grant camera and microphone access and complete the device checks.
5. Select **Continue to eSignet**, enter the fictional identity number matching the invited name, enter OTP `111111`, and approve sharing the name.
6. After returning to AssessBridge, take a live session photo and continue.
7. Read and acknowledge the instructions, then select **Start exam**.
8. Answer the questions and submit. When the timer expires, saved progress is submitted automatically.
9. Wait for the recording upload to finish before selecting **Exit exam**.

The platform-account password, exam-only password and SEB configuration password serve different purposes. The emergency SEB exit code is also included in the invitation. Explicitly leaving after starting and before submitting disqualifies the attempt; a lost connection is handled through the recovery workflow rather than treated as proof of a voluntary exit.

### 14. Review, audit and publish the result

1. Sign in as employer personnel and open the candidate's session under **Review case**.
2. Wait for recording analysis to complete. Review the answers, integrity observations, recordings and connectivity report.
3. Grade written responses, record findings as appropriate and save a review decision with a reason.
4. Wait for the assessment's scheduled access window to close. Submitting an attempt early does not close the assessment window.
5. Sign in as the company's auditor and open the closed assessment under **Assessment auditing**.
6. Inspect each candidate's report and record the audit outcome. Resolve outstanding review or audit issues before marking the candidate as audited.
7. Once all candidates satisfy the audit requirements, select **Approve and publish**, enter a remark and confirm.
8. Sign in as the candidate and open **History** to view the published result.

Candidate scores become visible after publication. Published results are final and the associated reviewer decisions and findings are locked.

## Fictional test identities

| Name to enter in the candidate roster | eSignet identity number | OTP |
| --- | --- | --- |
| Daniel Bekele | `900000000001` | `111111` |
| Samuel Mensah | `900000000002` | `111111` |
| Grace Njeri | `900000000003` | `111111` |

Invite the candidate using one of these names and the tester's actual accessible email address. During authentication, use the identity number from the same row. Multiple testers can use the fictional identities with their own candidate accounts and invitations.

The provider authenticates fictional records and returns their names; it does not connect to Ethiopia's live Fayda registry. Each tester captures their own live photo, which becomes the reference for monitoring that exam session. Identity definitions are maintained in [infra/esignet/identities.json](infra/esignet/identities.json).

## Running the tests

Install the development requirements inside the active environment:

```sh
python -m pip install -r backend/requirements-dev.txt
```

This includes the runtime requirements and the HTTP test client. Use the provided runner:

| Command | Coverage | Additional requirements |
| --- | --- | --- |
| `python backend/run_tests.py` | Regular tests with controlled dependencies | No running database, browser or identity provider |
| `python backend/run_tests.py --integration` | Exam APIs and identity verification with a mocked provider | MySQL and permission to create/drop test databases |
| `python backend/run_tests.py --browser` | Account and candidate UI flows with controlled API replies | Chrome or Chromium |
| `python backend/run_tests.py --live-esignet` | Real OTP, consent and signed authentication returns for the fictional identities | MySQL and the configured eSignet services running |

Flags can be combined:

```sh
python backend/run_tests.py --integration --browser --live-esignet
```

Database suites create randomly named `assessbridge_test_...` databases and remove them afterward. They do not use the application database. The local test-database grant in step 4 permits these operations with MySQL 8.0's default database-grant behavior. Tests do not send real emails.

Browser tests discover installed Chrome/Chromium. If the executable is elsewhere, set its absolute path in the `CHROME_BIN` environment variable. Native SEB operation, real email delivery and actual camera/microphone permissions also require manual checks; mocked browser tests do not validate those device integrations.

## Starting and stopping an existing installation

For a subsequent local session:

1. Start MySQL and Docker.
2. Open a terminal in the repository and activate `.venv` using the command for the current shell.
3. Run the eSignet setup command from step 9 to prepare/start the identity services with the configured local addresses.
4. Start FastAPI with the command from step 10.

Stop FastAPI with `Ctrl+C`. To stop the identity services while preserving their data:

```sh
python backend/setup_esignet.py --stop
```

The setup command reuses existing signing material and identity data. Keep `var/esignet/` and the identity Docker volumes together when preserving an installation. Avoid recreating `.env` or generating new encryption keys on every startup.

## Configuration notes

| Setting | Purpose |
| --- | --- |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Application MySQL connection |
| `JWT_SECRET` | Authentication signing secret |
| `RECORDING_KEY` | Encryption key for recordings and identity evidence |
| `SMTP_*` | Verification, account-recovery and invitation email delivery |
| `EXAM_ENTRY_URL`, `SEB_BASE_URL` | Application addresses used in links and SEB checks |
| `SEB_REQUIRED` | Require SEB or allow browser-only development sessions |
| `SEB_CONFIG_KEY`, `SEB_CONFIG_PASSWORD`, `SEB_QUIT_PASSWORD` | Generic SEB settings written by the configuration generator |
| `ESIGNET_*` | Identity issuer, application client and signing configuration written by the setup script |
| `MONITORING_AUTO_START`, `MONITORING_CONCURRENCY` | Automatic worker startup and analysis concurrency |
| `FFMPEG_CMD` | Optional absolute FFmpeg executable path |
| `RECORDINGS_DIR` | Local recording storage directory |
| `IDENTITY_RETENTION_DAYS`, `RECORDING_RETENTION_DAYS` | Default identity-image and recording retention periods |
| `SECURITY_AUDIT_RETENTION_DAYS` | Optional expiry for newly recorded authentication/staff-access audit events |

Restart FastAPI after changing configuration. The application also supports process environment variables; pre-existing environment values can take precedence over `.env` settings loaded at startup.

To use an ordinary browser for development, stop FastAPI, set `SEB_REQUIRED=false` in `.env`, restart and create fresh invitations. The SEB generator sets this option back to `true` when run. Existing invitations contain their original links and configuration; generate new invitations when changing addresses or exam-entry mode.

Company administrators manage retention under **Company settings**. Identity images and recordings default to 30 days. Case records and assessment-audit metadata have no automatic expiry until a company policy sets it. Policy changes apply to new assessments; existing assessments retain their recorded policy. Downloaded exports and backups are managed separately.

Non-local installations require HTTPS application and identity-provider origins, secure cookies, a reachable email configuration and appropriately provisioned storage and workers. The local command above binds only to `127.0.0.1`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `python` or `py` is not found | Verify Python installation and terminal `PATH`; reopen the terminal after installing Python. Use `python3` to create the environment on Linux/macOS. |
| PowerShell refuses environment activation | Use the process-scoped activation-policy command in step 3 or activate the environment from Git Bash. |
| MySQL connection refused or access denied | Confirm the server is running on the configured port. Verify the account from step 4 and the password in `.env`; the documented account uses TCP host `127.0.0.1`. |
| Missing tables or columns | Run `python backend/init_db.py` with the intended `.env` settings before starting FastAPI. |
| Company activation or invitations are not delivered | Check SMTP credentials, sender authorization, app-password availability and the recipient's spam folder. Inspect server logs for diagnostics. A running web page does not establish email delivery. |
| `Set JWT_SECRET...` or an invalid recording-key error | Replace both placeholders in `.env` using the separate generators in step 5. |
| Docker is unavailable | Start the Docker engine, verify `docker version` and `docker compose version`, and confirm Linux-container mode on Windows. For WSL-only Docker, check the `Ubuntu` commands in step 1. |
| eSignet authentication is unavailable | Finish step 9, verify the discovery URL and inspect identity-container logs. The issuer, callback and application origin must match the setup command and `.env`. |
| SEB configuration is unavailable | Run step 10 after identity setup, restart FastAPI and use the configuration supplied with the specific exam invitation. |
| The assessment is not open | Check the invitation's opening and closing times and the local system clock. Candidates cannot start outside the scheduled window. |
| Authentication reports a name mismatch | Match the roster name to the selected fictional identity exactly; use the identity number from the same table row. |
| Models are missing or a checksum fails | Re-run the model downloads after restoring internet access. Wait for successful verification before starting the worker. |
| Analysis stays pending | Inspect `var/monitoring-worker.log`, verify all model downloads and FFmpeg, and confirm `MONITORING_AUTO_START=true`. Keep FastAPI running while analysis completes. |
| Camera or microphone access fails | Check browser/SEB permissions, connected devices and whether another application is using them. Use the documented loopback origin for local setup or HTTPS for remote access. |
| The auditor cannot find an assessment | Confirm company membership and wait for the scheduled assessment window to close. |
| A candidate cannot see a score | Complete reviewer grading/decision, audit every candidate and publish the assessment results. |
| Integration tests cannot create their database | Verify the test-database grant from step 4. Custom MySQL privilege settings may require a separately authorized test account. |

When Docker is available directly in the terminal, inspect the identity stack with:

```sh
docker compose --env-file var/esignet/runtime.env -f infra/esignet/compose.yaml ps
docker compose --env-file var/esignet/runtime.env -f infra/esignet/compose.yaml logs --tail 100 esignet mock-identity-system esignet-ui
```

For WSL-only Docker, prefix those commands with `wsl -d Ubuntu -u root --` and use the corresponding `/mnt/<drive>/...` paths for the two configuration files.

## Repository layout

```text
AssessBridge/
├── backend/
│   ├── app/                     FastAPI routes, domain logic and monitoring worker
│   ├── models/                  Model manifest and provenance; downloaded weights are ignored
│   ├── seb/                     SEB documentation and generated local configuration
│   ├── tests/                   Unit, API, browser and live-provider tests
│   ├── init_db.py               Application schema initialization and migrations
│   ├── fetch_identity_models.py Face-model downloads
│   ├── fetch_monitoring_models.py Monitoring models and Windows FFmpeg downloads
│   ├── setup_esignet.py         Identity-stack bootstrap and lifecycle commands
│   ├── make_seb_config.py       Generic local SEB configuration generator
│   └── run_tests.py             Isolated test runner
├── frontend/                   HTML pages, styles, scripts and branding
├── infra/esignet/              Identity-service Compose configuration and demo definitions
├── var/                        Generated keys, recordings, tools and logs; ignored by Git
├── .env.example                Committed configuration template
├── .gitattributes              Cross-platform shell-script line-ending rules
└── .gitignore                  Local secrets and generated-file exclusions
```

## Third-party components

Model sources, pinned versions and license information are documented in [MONITORING_MODELS.md](backend/models/MONITORING_MODELS.md). The model release includes the RT-DETR license. The identity integration includes upstream [license](infra/esignet/upstream/LICENSE) and [notice](infra/esignet/upstream/NOTICE.txt) files. These components retain their respective upstream licenses.
