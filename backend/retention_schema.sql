CREATE TABLE IF NOT EXISTS company_retention_policies (
 company_id BIGINT UNSIGNED PRIMARY KEY,revision INT UNSIGNED NOT NULL DEFAULT 0,
 identity_days SMALLINT UNSIGNED NOT NULL,monitoring_days SMALLINT UNSIGNED NOT NULL,
 review_days SMALLINT UNSIGNED NULL,audit_days SMALLINT UNSIGNED NULL,
 FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS assessment_retention_policies (
 assessment_id BIGINT UNSIGNED PRIMARY KEY,revision INT UNSIGNED NOT NULL,
 identity_days SMALLINT UNSIGNED NOT NULL,monitoring_days SMALLINT UNSIGNED NOT NULL,
 review_days SMALLINT UNSIGNED NULL,audit_days SMALLINT UNSIGNED NULL,legacy BOOLEAN NOT NULL DEFAULT FALSE,cleanup_attempt_at DATETIME NULL,
 FOREIGN KEY(assessment_id) REFERENCES assessments(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS company_retention_changes (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,company_id BIGINT UNSIGNED NOT NULL,
 revision INT UNSIGNED NOT NULL,request_key CHAR(36) NOT NULL,payload_hash CHAR(64) NOT NULL,
 actor_id BIGINT UNSIGNED NOT NULL,actor_name VARCHAR(254) NOT NULL,reason VARCHAR(1000) NOT NULL,
 before_json JSON NOT NULL,after_json JSON NOT NULL,created_at DATETIME(3) NOT NULL,expires_at DATETIME(3) NULL,
 UNIQUE KEY uq_retention_request(company_id,request_key),FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS retained_assessment_audits (
 assessment_id BIGINT UNSIGNED PRIMARY KEY,company_id BIGINT UNSIGNED NOT NULL,
 closed_at DATETIME NOT NULL,case_removed_at DATETIME(3) NOT NULL,expires_at DATETIME(3) NULL,
 policy_json JSON NOT NULL,events_json JSON NOT NULL,
 FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE,
 INDEX idx_retained_expiry(expires_at)
);
