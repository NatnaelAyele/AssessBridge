CREATE TABLE IF NOT EXISTS assessment_case_notes (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,session_id BIGINT UNSIGNED NOT NULL,revision INT UNSIGNED NOT NULL,
 request_key CHAR(36) NOT NULL,payload_hash CHAR(64) NOT NULL,body TEXT NOT NULL,reference_kind VARCHAR(20) NULL,reference_key VARCHAR(100) NULL,
 actor_id BIGINT UNSIGNED NOT NULL,actor_name VARCHAR(160) NOT NULL,created_at DATETIME(3) NOT NULL,
 UNIQUE KEY uq_note_request(session_id,request_key),FOREIGN KEY(session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE,FOREIGN KEY(actor_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS assessment_audit_outcomes (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,session_id BIGINT UNSIGNED NOT NULL,kind ENUM('needs_attention','audited') NOT NULL,
 reason TEXT NOT NULL,evidence_version CHAR(64) NOT NULL,actor_id BIGINT UNSIGNED NOT NULL,actor_name VARCHAR(160) NOT NULL,
 request_key CHAR(36) NULL,payload_hash CHAR(64) NULL,created_at DATETIME(3) NOT NULL,
 UNIQUE KEY uq_outcome_request(session_id,request_key),FOREIGN KEY(session_id) REFERENCES assessment_exam_sessions(id) ON DELETE CASCADE,FOREIGN KEY(actor_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS assessment_publication_holds (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,assessment_id BIGINT UNSIGNED NOT NULL,kind ENUM('held','resolved') NOT NULL,
 reason TEXT NOT NULL,actor_id BIGINT UNSIGNED NOT NULL,actor_name VARCHAR(160) NOT NULL,request_key CHAR(36) NOT NULL,payload_hash CHAR(64) NOT NULL,created_at DATETIME(3) NOT NULL,
 UNIQUE KEY uq_hold_request(assessment_id,request_key),FOREIGN KEY(assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,FOREIGN KEY(actor_id) REFERENCES users(id)
);
