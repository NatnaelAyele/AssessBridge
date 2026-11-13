CREATE TABLE IF NOT EXISTS companies (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(160) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS company_staff_invitations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    company_id BIGINT UNSIGNED NOT NULL,
    email VARCHAR(254) COLLATE utf8mb4_unicode_ci NOT NULL,
    full_name VARCHAR(160) NOT NULL,
    role ENUM('company_admin','employer_personnel','auditor') NOT NULL,
    token_hash CHAR(64) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    invited_by BIGINT UNSIGNED NOT NULL,
    accepted_by BIGINT UNSIGNED NULL,
    accepted_at DATETIME NULL,
    revoked_at DATETIME NULL,
    sent_at DATETIME NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    FOREIGN KEY (invited_by) REFERENCES users(id),
    FOREIGN KEY (accepted_by) REFERENCES users(id),
    INDEX idx_company_invitation_email (company_id,email)
);

CREATE TABLE IF NOT EXISTS company_registration_verifications (
    user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    company_id BIGINT UNSIGNED NOT NULL,
    token_hash CHAR(64) NOT NULL UNIQUE,
    expires_at DATETIME NOT NULL,
    sent_at DATETIME NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS company_account_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    company_id BIGINT UNSIGNED NOT NULL,
    actor_user_id BIGINT UNSIGNED NULL,
    target_user_id BIGINT UNSIGNED NULL,
    invitation_id BIGINT UNSIGNED NULL,
    event_type VARCHAR(50) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES companies(id),
    FOREIGN KEY (actor_user_id) REFERENCES users(id),
    FOREIGN KEY (target_user_id) REFERENCES users(id),
    FOREIGN KEY (invitation_id) REFERENCES company_staff_invitations(id),
    INDEX idx_company_events (company_id,created_at)
);

CREATE TABLE IF NOT EXISTS company_password_resets (
 user_id BIGINT UNSIGNED PRIMARY KEY,
 token_hash CHAR(64) NOT NULL UNIQUE,
 expires_at DATETIME NOT NULL,
 FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
