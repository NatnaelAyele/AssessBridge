CREATE TABLE IF NOT EXISTS assessment_export_audit (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, assessment_id BIGINT UNSIGNED NOT NULL, invitation_id BIGINT UNSIGNED NULL,
 actor_id BIGINT UNSIGNED NOT NULL, actor_name VARCHAR(160) NOT NULL, format ENUM('csv','pdf') NOT NULL,
 publication_id BIGINT UNSIGNED NULL, source_version VARCHAR(160) NOT NULL, filters_json JSON NOT NULL,
 record_count INT UNSIGNED NOT NULL, content_sha256 CHAR(64) NOT NULL, generated_at DATETIME(3) NOT NULL,
 FOREIGN KEY(assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,FOREIGN KEY(actor_id) REFERENCES users(id),
 INDEX idx_export_assessment(assessment_id,generated_at)
);
