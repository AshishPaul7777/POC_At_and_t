-- LWC and Aura enter the judged component set.
--
-- Until now they were retrieved and searched as *reference sources* only.
-- They were never inventoried as components, so they could never receive a
-- USED / UNUSED / NEEDS_REVIEW verdict. FlexiPage placement and cross-bundle
-- imports are Tier-A use for these types (unlike CustomField-on-Layout).

ALTER TYPE component_type ADD VALUE IF NOT EXISTS 'LightningComponentBundle';
ALTER TYPE component_type ADD VALUE IF NOT EXISTS 'AuraDefinitionBundle';
