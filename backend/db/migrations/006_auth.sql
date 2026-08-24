-- Application users.
--
-- Two roles, deliberately. `admin` can manage users; `user` can do everything
-- else the app offers. There is no per-page permission matrix because there is
-- nothing here worth hiding from someone already trusted to see the org's
-- metadata -- the meaningful boundary is who can grant access to it.
--
-- Passwords are stored as scrypt hashes with a per-user salt, in the PHC-style
-- string produced by app/auth/passwords.py. Nothing here ever holds a plaintext
-- password, and the column is deliberately named for what it contains.

CREATE TABLE IF NOT EXISTS app_user (
  id            BIGSERIAL   PRIMARY KEY,
  -- Stored lower-cased; the unique index is what actually stops
  -- Hritik@x.com and hritik@x.com becoming two accounts.
  email         TEXT        NOT NULL,
  password_hash TEXT        NOT NULL,
  role          TEXT        NOT NULL DEFAULT 'user',
  is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
  -- The env-provisioned account. Marked so the UI can stop an admin deleting
  -- or demoting the one login guaranteed to work after a fresh deploy.
  is_superuser  BOOLEAN     NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by    TEXT,
  last_login_at TIMESTAMPTZ,
  CONSTRAINT app_user_role_ck CHECK (role IN ('admin', 'user'))
);

CREATE UNIQUE INDEX IF NOT EXISTS app_user_email_uq
  ON app_user (lower(email));

-- Which AUTH_SUPERUSER_PASSWORD was last applied, stored as its own hash.
-- Without this, startup would overwrite the superuser's password from the
-- environment on every boot, silently reverting a password the superuser had
-- changed in the UI. With it, the environment only wins when it actually
-- changes -- which keeps "edit .env and restart" working as a password reset.
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS bootstrap_fingerprint TEXT;
