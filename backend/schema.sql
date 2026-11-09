CREATE TABLE IF NOT EXISTS users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) COLLATE utf8mb4_bin NOT NULL UNIQUE,
    email VARCHAR(254) COLLATE utf8mb4_unicode_ci NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('candidate', 'employer_personnel', 'auditor') NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_id CHAR(64) NOT NULL PRIMARY KEY,
    user_id BIGINT UNSIGNED NOT NULL,
    expires_at DATETIME NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_auth_session_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_auth_sessions_user (user_id),
    INDEX idx_auth_sessions_expiry (expires_at)
);

CREATE TABLE IF NOT EXISTS candidate_email_verifications (
    user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    token_hash CHAR(64) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    sent_at DATETIME NOT NULL,
    CONSTRAINT fk_email_verification_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS auth_audit (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT UNSIGNED NULL,
    username_attempt VARCHAR(100) NULL,
    event_type ENUM('login_success', 'login_failure', 'authorization_denied', 'logout') NOT NULL,
    source_ip VARCHAR(45) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_auth_audit_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_auth_audit_user (user_id),
    INDEX idx_auth_audit_created (created_at)
);

CREATE TABLE IF NOT EXISTS assessments (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    employer_user_id BIGINT UNSIGNED NOT NULL,
    request_key CHAR(36) NOT NULL UNIQUE,
    payload_hash CHAR(64) NOT NULL,
    title VARCHAR(120) NOT NULL,
    description TEXT NULL,
    internal_reference VARCHAR(60) NULL,
    duration_minutes SMALLINT UNSIGNED NOT NULL,
    rules_text TEXT NULL,
    identity_policy TEXT NULL,
    questions_json JSON NOT NULL,
    starts_at DATETIME NOT NULL,
    ends_at DATETIME NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_assessment_employer FOREIGN KEY (employer_user_id) REFERENCES users(id),
    INDEX idx_assessment_employer (employer_user_id)
);

CREATE TABLE IF NOT EXISTS assessment_invitations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    assessment_id BIGINT UNSIGNED NOT NULL,
    candidate_name VARCHAR(120) NOT NULL,
    candidate_email VARCHAR(254) NOT NULL,
    candidate_user_id BIGINT UNSIGNED NULL,
    exam_username VARCHAR(64) COLLATE utf8mb4_bin NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    delivery_status ENUM('pending', 'sent', 'failed') NOT NULL DEFAULT 'pending',
    sent_at DATETIME NULL,
    seb_config_key CHAR(64) NULL,
    seb_config_blob LONGBLOB NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_invitation_assessment FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,
    CONSTRAINT fk_invitation_candidate_user FOREIGN KEY (candidate_user_id) REFERENCES users(id),
    UNIQUE KEY uq_assessment_candidate_email (assessment_id, candidate_email),
    INDEX idx_invitation_status (assessment_id, delivery_status)
);

CREATE TABLE IF NOT EXISTS assessment_activity (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    assessment_id BIGINT UNSIGNED NOT NULL,
    actor_user_id BIGINT UNSIGNED NOT NULL,
    event_type ENUM('assessment_created', 'invitation_sent', 'invitation_failed') NOT NULL,
    invitation_id BIGINT UNSIGNED NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_activity_assessment FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,
    CONSTRAINT fk_activity_actor FOREIGN KEY (actor_user_id) REFERENCES users(id),
    INDEX idx_activity_assessment (assessment_id)
);

CREATE TABLE IF NOT EXISTS assessment_consents (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    invitation_id BIGINT UNSIGNED NOT NULL,
    candidate_user_id BIGINT UNSIGNED NOT NULL,
    policy_version VARCHAR(30) NOT NULL,
    accepted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_consent_invitation FOREIGN KEY (invitation_id) REFERENCES assessment_invitations(id) ON DELETE CASCADE,
    CONSTRAINT fk_consent_candidate FOREIGN KEY (candidate_user_id) REFERENCES users(id),
    UNIQUE KEY uq_consent_invitation_candidate_version (invitation_id, candidate_user_id, policy_version)
);

CREATE TABLE IF NOT EXISTS assessment_identity_checks (
    invitation_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    candidate_user_id BIGINT UNSIGNED NOT NULL,
    access_fingerprint CHAR(64) NULL,
    status ENUM('passed', 'failed') NOT NULL,
    stage VARCHAR(30) NULL,
    failure_reason VARCHAR(50) NULL,
    card_detection_method VARCHAR(30) NULL,
    name_readability DECIMAL(5,4) NULL,
    name_similarity DECIMAL(5,4) NULL,
    face_similarity DECIMAL(6,4) NULL,
    attempts SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    checked_at DATETIME NOT NULL,
    evidence_expires_at DATETIME NOT NULL,
    card_ciphertext LONGBLOB NULL,
    selfie_ciphertext LONGBLOB NULL,
    CONSTRAINT fk_identity_invitation FOREIGN KEY (invitation_id) REFERENCES assessment_invitations(id) ON DELETE CASCADE,
    CONSTRAINT fk_identity_candidate FOREIGN KEY (candidate_user_id) REFERENCES users(id),
    INDEX idx_identity_evidence_expiry (evidence_expires_at)
);

CREATE TABLE IF NOT EXISTS assessment_identity_attempts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    invitation_id BIGINT UNSIGNED NOT NULL,
    candidate_user_id BIGINT UNSIGNED NOT NULL,
    attempt_number SMALLINT UNSIGNED NOT NULL,
    status ENUM('passed', 'failed') NOT NULL,
    stage VARCHAR(30) NOT NULL,
    failure_reason VARCHAR(50) NULL,
    card_detection_method VARCHAR(30) NULL,
    name_readability DECIMAL(5,4) NULL,
    name_similarity DECIMAL(5,4) NULL,
    face_similarity DECIMAL(6,4) NULL,
    checked_at DATETIME NOT NULL,
    CONSTRAINT fk_identity_attempt_invitation FOREIGN KEY (invitation_id) REFERENCES assessment_invitations(id) ON DELETE CASCADE,
    CONSTRAINT fk_identity_attempt_candidate FOREIGN KEY (candidate_user_id) REFERENCES users(id),
    UNIQUE KEY uq_identity_attempt_number (invitation_id, attempt_number),
    INDEX idx_identity_attempt_invitation_time (invitation_id, checked_at)
);

CREATE TABLE IF NOT EXISTS assessment_exam_sessions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    invitation_id BIGINT UNSIGNED NOT NULL UNIQUE,
    candidate_user_id BIGINT UNSIGNED NOT NULL,
    auth_session_token_id CHAR(64) NULL,
    started_at DATETIME NOT NULL,
    deadline_at DATETIME NOT NULL,
    current_index SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    submitted_at DATETIME NULL,
    submission_reason ENUM('manual', 'timeout') NULL,
    last_seen_at DATETIME NULL,
    disqualified_at DATETIME NULL,
    disqualification_reason ENUM('early_exit', 'connection_lost') NULL,
    interrupted_at DATETIME NULL,
    interruption_reason VARCHAR(40) NULL,
    recovery_started_at DATETIME(3) NULL,
    recovery_reason VARCHAR(40) NULL,
    recovery_seconds DECIMAL(12,3) NOT NULL DEFAULT 0,
    client_instance_id VARCHAR(36) NULL,
    resume_required BOOLEAN NOT NULL DEFAULT FALSE,
    resume_identity_client VARCHAR(36) NULL,
    CONSTRAINT fk_exam_session_invitation FOREIGN KEY (invitation_id) REFERENCES assessment_invitations(id) ON DELETE CASCADE,
    CONSTRAINT fk_exam_session_candidate FOREIGN KEY (candidate_user_id) REFERENCES users(id),
    INDEX idx_exam_session_deadline (submitted_at, deadline_at)
);

CREATE TABLE IF NOT EXISTS assessment_exam_answers (
    session_id BIGINT UNSIGNED NOT NULL,
    question_index SMALLINT UNSIGNED NOT NULL,
    answer_json JSON NOT NULL,
    saved_at DATETIME NOT NULL,
    PRIMARY KEY (session_id, question_index),
    CONSTRAINT fk_exam_answer_session FOREIGN KEY (session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS assessment_connectivity_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    session_id BIGINT UNSIGNED NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    occurred_at DATETIME(3) NOT NULL,
    details_json JSON NOT NULL,
    CONSTRAINT fk_connectivity_session FOREIGN KEY(session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE,
    INDEX idx_connectivity_session(session_id,occurred_at)
);

CREATE TABLE IF NOT EXISTS assessment_network_readiness (
    invitation_id BIGINT UNSIGNED NOT NULL,
    client_instance_id VARCHAR(36) NOT NULL,
    checked_at DATETIME NOT NULL,
    upload_started_at DATETIME(3) NULL,
    upload_bytes INT NOT NULL DEFAULT 0,
    upload_ms INT NOT NULL DEFAULT 0,
    passed BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY(invitation_id,client_instance_id),
    CONSTRAINT fk_readiness_invitation FOREIGN KEY(invitation_id) REFERENCES assessment_invitations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS exam_service_health (
    id INT PRIMARY KEY,
    seen_at DATETIME(3) NOT NULL
);

CREATE TABLE IF NOT EXISTS assessment_recordings (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    session_id BIGINT UNSIGNED NOT NULL UNIQUE,
    storage_token CHAR(32) COLLATE utf8mb4_bin NOT NULL UNIQUE,
    status ENUM('recording', 'complete', 'incomplete', 'deleted') NOT NULL DEFAULT 'recording',
    started_at DATETIME NOT NULL,
    finalized_at DATETIME NULL,
    expires_at DATETIME NULL,
    CONSTRAINT fk_recording_session FOREIGN KEY (session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE,
    INDEX idx_recording_expiry (expires_at)
);

CREATE TABLE IF NOT EXISTS assessment_recording_parts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    recording_id BIGINT UNSIGNED NOT NULL,
    mime_type VARCHAR(120) NOT NULL,
    status ENUM('recording', 'complete', 'incomplete') NOT NULL DEFAULT 'recording',
    started_at DATETIME NOT NULL,
    finalized_at DATETIME NULL,
    total_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0,
    CONSTRAINT fk_recording_part FOREIGN KEY (recording_id) REFERENCES assessment_recordings(id) ON DELETE CASCADE,
    INDEX idx_recording_part (recording_id, id)
);

CREATE TABLE IF NOT EXISTS assessment_recording_chunks (
    part_id BIGINT UNSIGNED NOT NULL,
    sequence_number INT UNSIGNED NOT NULL,
    size_bytes INT UNSIGNED NOT NULL,
    sha256 CHAR(64) NOT NULL,
    received_at DATETIME NOT NULL,
    PRIMARY KEY (part_id, sequence_number),
    CONSTRAINT fk_recording_chunk_part FOREIGN KEY (part_id) REFERENCES assessment_recording_parts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS assessment_recording_access_audit (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    recording_id BIGINT UNSIGNED NOT NULL,
    actor_user_id BIGINT UNSIGNED NOT NULL,
    accessed_at DATETIME NOT NULL,
    source_ip VARCHAR(45) NULL,
    CONSTRAINT fk_recording_audit_recording FOREIGN KEY (recording_id) REFERENCES assessment_recordings(id) ON DELETE CASCADE,
    CONSTRAINT fk_recording_audit_user FOREIGN KEY (actor_user_id) REFERENCES users(id),
    INDEX idx_recording_access (recording_id, accessed_at)
);

CREATE TABLE IF NOT EXISTS assessment_exam_interruptions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    invitation_id BIGINT UNSIGNED NOT NULL,
    candidate_user_id BIGINT UNSIGNED NOT NULL,
    event_type ENUM('exit_requested', 'resumed') NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_exam_interruption_invitation FOREIGN KEY (invitation_id) REFERENCES assessment_invitations(id) ON DELETE CASCADE,
    CONSTRAINT fk_exam_interruption_candidate FOREIGN KEY (candidate_user_id) REFERENCES users(id),
    INDEX idx_exam_interruption_invitation (invitation_id, created_at)
);

CREATE TABLE IF NOT EXISTS assessment_monitoring_runs (
    session_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    policy_json JSON NOT NULL,
    capabilities_json JSON NOT NULL,
    reference_ciphertext LONGBLOB NULL,
    reference_expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_monitor_run_session FOREIGN KEY (session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS assessment_monitoring_jobs (
    part_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    session_id BIGINT UNSIGNED NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INT UNSIGNED NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL,
    error_code VARCHAR(60) NULL,
    model_version VARCHAR(150) NULL,
    CONSTRAINT fk_monitor_job_part FOREIGN KEY (part_id) REFERENCES assessment_recording_parts(id) ON DELETE CASCADE,
    CONSTRAINT fk_monitor_job_run FOREIGN KEY (session_id) REFERENCES assessment_monitoring_runs(session_id) ON DELETE CASCADE,
    INDEX idx_monitor_job_status (status, updated_at)
);
CREATE TABLE IF NOT EXISTS assessment_monitoring_samples (
    part_id BIGINT UNSIGNED NOT NULL,
    stream VARCHAR(8) NOT NULL,
    start_ms BIGINT UNSIGNED NOT NULL,
    end_ms BIGINT UNSIGNED NOT NULL,
    metrics_json JSON NOT NULL,
    PRIMARY KEY (part_id, stream, start_ms),
    CONSTRAINT fk_monitor_sample_job FOREIGN KEY (part_id) REFERENCES assessment_monitoring_jobs(part_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS assessment_monitoring_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    event_key CHAR(64) NOT NULL UNIQUE,
    session_id BIGINT UNSIGNED NOT NULL,
    part_id BIGINT UNSIGNED NULL,
    event_type VARCHAR(60) NOT NULL,
    category VARCHAR(30) NOT NULL,
    start_ms BIGINT UNSIGNED NOT NULL,
    end_ms BIGINT UNSIGNED NOT NULL,
    confidence FLOAT NULL,
    closed BOOLEAN NOT NULL DEFAULT FALSE,
    details_json JSON NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    CONSTRAINT fk_monitor_event_run FOREIGN KEY (session_id) REFERENCES assessment_monitoring_runs(session_id) ON DELETE CASCADE,
    CONSTRAINT fk_monitor_event_part FOREIGN KEY (part_id) REFERENCES assessment_recording_parts(id) ON DELETE SET NULL,
    INDEX idx_monitor_event_session (session_id, start_ms)
);

CREATE TABLE IF NOT EXISTS monitoring_worker_health (
    id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
    heartbeat_at DATETIME NOT NULL,
    model_version VARCHAR(150) NOT NULL
);

CREATE TABLE IF NOT EXISTS assessment_review_state (
 session_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
 revision INT UNSIGNED NOT NULL DEFAULT 0,
 CONSTRAINT fk_review_state_session FOREIGN KEY(session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS assessment_review_decisions (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 session_id BIGINT UNSIGNED NOT NULL,
 revision INT UNSIGNED NOT NULL,
 request_key CHAR(36) NOT NULL,
 payload_hash CHAR(64) NOT NULL,
 action ENUM('keep_score','change_score','disqualify') NOT NULL,
 reason TEXT NOT NULL,
 final_score DECIMAL(5,2) NULL,
 previous_json JSON NOT NULL,
 grades_json JSON NOT NULL,
 event_keys_json JSON NOT NULL,
 reviewer_user_id BIGINT UNSIGNED NOT NULL,
 reviewer_username VARCHAR(100) NOT NULL,
 created_at DATETIME NOT NULL,
 UNIQUE KEY uq_review_decision_revision(session_id,revision),
 UNIQUE KEY uq_review_decision_request(session_id,request_key),
 CONSTRAINT fk_review_decision_session FOREIGN KEY(session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE,
 CONSTRAINT fk_review_decision_reviewer FOREIGN KEY(reviewer_user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS assessment_review_findings (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 session_id BIGINT UNSIGNED NOT NULL,
 event_key CHAR(64) NOT NULL,
 revision INT UNSIGNED NOT NULL,
 request_key CHAR(36) NOT NULL,
 payload_hash CHAR(64) NOT NULL,
 finding ENUM('supports_violation','dismissed','inconclusive') NOT NULL,
 reason TEXT NOT NULL,
 event_snapshot_json JSON NOT NULL,
 reviewer_user_id BIGINT UNSIGNED NOT NULL,
 reviewer_username VARCHAR(100) NOT NULL,
 created_at DATETIME NOT NULL,
 UNIQUE KEY uq_review_finding_revision(session_id,revision),
 UNIQUE KEY uq_review_finding_request(session_id,request_key),
 INDEX idx_review_finding_event(session_id,event_key),
 CONSTRAINT fk_review_finding_session FOREIGN KEY(session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE,
 CONSTRAINT fk_review_finding_reviewer FOREIGN KEY(reviewer_user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS assessment_connectivity_findings (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 session_id BIGINT UNSIGNED NOT NULL,
 incident_event_id BIGINT UNSIGNED NOT NULL,
 revision INT UNSIGNED NOT NULL,
 request_key CHAR(36) NOT NULL,
 payload_hash CHAR(64) NOT NULL,
 finding ENUM('explained','unresolved','needs_attention') NOT NULL,
 reason TEXT NOT NULL,
 incident_snapshot_json JSON NOT NULL,
 reviewer_user_id BIGINT UNSIGNED NOT NULL,
 reviewer_username VARCHAR(100) NOT NULL,
 created_at DATETIME NOT NULL,
 UNIQUE KEY uq_connectivity_finding_revision(session_id,revision),
 UNIQUE KEY uq_connectivity_finding_request(session_id,request_key),
 INDEX idx_connectivity_finding_incident(session_id,incident_event_id),
 CONSTRAINT fk_connectivity_finding_session FOREIGN KEY(session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE,
 CONSTRAINT fk_connectivity_finding_event FOREIGN KEY(incident_event_id) REFERENCES assessment_connectivity_events(id) ON DELETE CASCADE,
 CONSTRAINT fk_connectivity_finding_reviewer FOREIGN KEY(reviewer_user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS assessment_review_access_audit (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 session_id BIGINT UNSIGNED NOT NULL,
 actor_user_id BIGINT UNSIGNED NOT NULL,
 resource VARCHAR(30) NOT NULL,
 accessed_at DATETIME NOT NULL,
 source_ip VARCHAR(45) NULL,
 CONSTRAINT fk_review_access_session FOREIGN KEY(session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE,
 CONSTRAINT fk_review_access_user FOREIGN KEY(actor_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS assessment_audit_completions (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 session_id BIGINT UNSIGNED NOT NULL,
 review_revision INT UNSIGNED NOT NULL,
 evidence_version CHAR(64) NOT NULL,
 auditor_user_id BIGINT UNSIGNED NOT NULL,
 auditor_username VARCHAR(100) NOT NULL,
 snapshot_json JSON NOT NULL,
 created_at DATETIME NOT NULL,
 UNIQUE KEY uq_audit_completion_version(session_id,evidence_version),
 CONSTRAINT fk_audit_completion_session FOREIGN KEY(session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE,
 CONSTRAINT fk_audit_completion_user FOREIGN KEY(auditor_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS assessment_result_publications (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 assessment_id BIGINT UNSIGNED NOT NULL,
 source_version CHAR(64) NOT NULL,
 publisher_user_id BIGINT UNSIGNED NOT NULL,
 publisher_username VARCHAR(100) NOT NULL,
 remark TEXT NOT NULL,
 candidate_count INT UNSIGNED NOT NULL,
 created_at DATETIME NOT NULL,
 is_legacy BOOLEAN NOT NULL DEFAULT FALSE,
 final_assessment_id BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN is_legacy=FALSE THEN assessment_id ELSE NULL END) VIRTUAL,
 UNIQUE KEY uq_final_publication_assessment(final_assessment_id),
 UNIQUE KEY uq_result_publication_version(assessment_id,source_version),
 CONSTRAINT fk_publication_assessment FOREIGN KEY(assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,
 CONSTRAINT fk_publication_publisher FOREIGN KEY(publisher_user_id) REFERENCES users(id),
 INDEX idx_publication_latest(assessment_id,id)
);

CREATE TABLE IF NOT EXISTS assessment_publication_results (
 publication_id BIGINT UNSIGNED NOT NULL,
 invitation_id BIGINT UNSIGNED NOT NULL,
 candidate_user_id BIGINT UNSIGNED NOT NULL,
 result_json JSON NOT NULL,
 PRIMARY KEY(publication_id,invitation_id),
 CONSTRAINT fk_published_result_publication FOREIGN KEY(publication_id) REFERENCES assessment_result_publications(id) ON DELETE CASCADE,
 CONSTRAINT fk_published_result_invitation FOREIGN KEY(invitation_id) REFERENCES assessment_invitations(id) ON DELETE CASCADE,
 CONSTRAINT fk_published_result_candidate FOREIGN KEY(candidate_user_id) REFERENCES users(id)
);
