-- Stable across backup/restore; independent installations cannot collide in a newsroom.
CREATE TABLE instance_identity (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
INSERT INTO instance_identity(id,value) VALUES(1,lower(hex(randomblob(16))));
