from django.db import migrations


NEW_TRIGGER = """
CREATE TRIGGER request_transition_guard BEFORE UPDATE OF status ON desk_datasetrequest
WHEN OLD.status != NEW.status BEGIN
    SELECT RAISE(ABORT, 'invalid transition') WHERE NOT (
        (OLD.status = 'submitted' AND NEW.status IN ('in_progress', 'cancelled')) OR
        (OLD.status = 'in_progress' AND NEW.status IN ('delivered', 'cancelled')) OR
        (OLD.status = 'delivered' AND NEW.status IN ('accepted', 'rejected')) OR
        (OLD.status = 'rejected' AND NEW.status = 'in_progress')
    );
    SELECT RAISE(ABORT, 'insufficient episodes') WHERE NEW.status = 'delivered'
        AND (SELECT COUNT(*) FROM desk_assignment WHERE request_id = OLD.id) < NEW.episodes_requested;
    SELECT RAISE(ABORT, 'cannot cancel a request with selected clips')
        WHERE NEW.status = 'cancelled' AND OLD.status = 'in_progress'
        AND EXISTS (SELECT 1 FROM desk_assignment WHERE request_id = OLD.id);
END
"""


OLD_TRIGGER = """
CREATE TRIGGER request_transition_guard BEFORE UPDATE OF status ON desk_datasetrequest
WHEN OLD.status != NEW.status BEGIN
    SELECT RAISE(ABORT, 'invalid transition') WHERE NOT (
        (OLD.status = 'submitted' AND NEW.status IN ('in_progress', 'cancelled')) OR
        (OLD.status = 'in_progress' AND NEW.status = 'delivered') OR
        (OLD.status = 'delivered' AND NEW.status IN ('accepted', 'rejected')) OR
        (OLD.status = 'rejected' AND NEW.status = 'in_progress')
    );
    SELECT RAISE(ABORT, 'insufficient episodes') WHERE NEW.status = 'delivered'
        AND (SELECT COUNT(*) FROM desk_assignment WHERE request_id = OLD.id) < NEW.episodes_requested;
END
"""


class Migration(migrations.Migration):
    dependencies = [("desk", "0006_add_cancelled_requests")]

    operations = [
        migrations.RunSQL("DROP TRIGGER request_transition_guard", OLD_TRIGGER),
        migrations.RunSQL(NEW_TRIGGER, "DROP TRIGGER request_transition_guard"),
    ]
