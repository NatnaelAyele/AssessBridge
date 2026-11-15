CREATE TABLE IF NOT EXISTS assessment_identity_auth_flows (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 invitation_id BIGINT UNSIGNED NOT NULL,
 candidate_user_id BIGINT UNSIGNED NOT NULL,
 access_fingerprint CHAR(64) NOT NULL,
 state_hash CHAR(64) NOT NULL UNIQUE,
 secret_ciphertext MEDIUMBLOB NOT NULL,
 status ENUM('pending','exchanging','authenticated','completed','failed') NOT NULL DEFAULT 'pending',
 authenticated_subject VARCHAR(255) NULL,
 authenticated_name VARCHAR(160) NULL,
 failure_reason VARCHAR(60) NULL,
 created_at DATETIME(3) NOT NULL,
 expires_at DATETIME(3) NOT NULL,
 authenticated_at DATETIME(3) NULL,
 CONSTRAINT fk_identity_flow_invitation FOREIGN KEY(invitation_id) REFERENCES assessment_invitations(id) ON DELETE CASCADE,
 CONSTRAINT fk_identity_flow_candidate FOREIGN KEY(candidate_user_id) REFERENCES users(id),
 INDEX idx_identity_flow_owner(invitation_id,candidate_user_id,id),
 INDEX idx_identity_flow_expiry(expires_at)
);
