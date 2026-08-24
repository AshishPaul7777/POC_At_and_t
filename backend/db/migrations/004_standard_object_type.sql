-- Standard objects are not custom objects.
--
-- They were being stored as ctype='CustomObject' with in_scope=false, because
-- the enum had no better option and they must exist in the graph: they are the
-- parents of their own custom fields (a third of this org's custom fields live
-- on Account, Lead, Case and friends) and they are reference sources in their
-- own right.
--
-- But the type column then states something false. Anyone reading the data — or
-- the UI — sees "Account, CustomObject, out of scope", which is confusing at
-- best and misleading at worst. A schema that needs a footnote to be read
-- correctly is a schema with a missing value.

ALTER TYPE component_type ADD VALUE IF NOT EXISTS 'StandardObject';

-- Reclassify what is already stored. Identified by the reason recorded at
-- inventory time rather than by guessing from the name, since a custom object
-- can be named anything.
UPDATE components
   SET ctype = 'StandardObject'
 WHERE ctype = 'CustomObject'
   AND in_scope = FALSE
   AND out_of_scope_reason = 'STANDARD_OBJECT';
