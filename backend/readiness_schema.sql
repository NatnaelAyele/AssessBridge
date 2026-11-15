CREATE TABLE IF NOT EXISTS assessment_readiness_records (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 invitation_id BIGINT UNSIGNED NOT NULL,candidate_user_id BIGINT UNSIGNED NOT NULL,
 client_instance_id CHAR(36) NOT NULL,access_fingerprint CHAR(64) NOT NULL,
 instructions_version CHAR(64) NOT NULL,policy_version VARCHAR(80) NOT NULL,
 checks_json JSON NOT NULL,acknowledgements_json JSON NOT NULL,
 seb_method VARCHAR(40) NOT NULL,client_version VARCHAR(180) NULL,
 platform VARCHAR(20) NOT NULL,passed BOOLEAN NOT NULL,created_at DATETIME(3) NOT NULL,
 CONSTRAINT fk_readiness_record_invitation FOREIGN KEY(invitation_id) REFERENCES assessment_invitations(id) ON DELETE CASCADE,
 INDEX idx_readiness_client(invitation_id,candidate_user_id,client_instance_id,created_at)
);
CREATE TABLE IF NOT EXISTS assessment_environment_events (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 invitation_id BIGINT UNSIGNED NOT NULL,candidate_user_id BIGINT UNSIGNED NOT NULL,
 client_instance_id CHAR(36) NOT NULL,request_key CHAR(36) NOT NULL,
 event_type VARCHAR(40) NOT NULL,client_occurred_at DATETIME(3) NOT NULL,
 received_at DATETIME(3) NOT NULL,
 CONSTRAINT fk_environment_invitation FOREIGN KEY(invitation_id) REFERENCES assessment_invitations(id) ON DELETE CASCADE,
 UNIQUE KEY uq_environment_event(invitation_id,request_key),
 INDEX idx_environment_invitation(invitation_id,id)
);
