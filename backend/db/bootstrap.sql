-- Run ONCE as the postgres superuser. Creates the app role and database.
-- You run this yourself so the superuser password never leaves your hands;
-- the app only ever uses the limited role created here.
--
--   psql -h 127.0.0.1 -U postgres -f backend/db/bootstrap.sql
--
-- Change the password below before running if this machine is shared.

CREATE ROLE sfcleanup WITH LOGIN PASSWORD 'sfcleanup_local_dev';

-- No superuser, no createrole, no createdb beyond what it owns.
ALTER ROLE sfcleanup NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION;

CREATE DATABASE sfcleanup OWNER sfcleanup ENCODING 'UTF8';

-- pg_trgm and uuid-ossp require elevated rights to install, so create them here
-- as superuser rather than inside schema.sql.
\connect sfcleanup
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
GRANT ALL ON SCHEMA public TO sfcleanup;
