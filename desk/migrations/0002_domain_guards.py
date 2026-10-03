from django.db import migrations


# Cross-table rules cannot be expressed as CHECK constraints. These SQLite
# triggers protect alternate write paths; services still provide useful errors.
TRIGGERS = {
    "assignment_insert_guard": """
        BEFORE INSERT ON desk_assignment BEGIN
            SELECT RAISE(ABORT, 'request must be in progress')
            WHERE (SELECT status FROM desk_datasetrequest WHERE id = NEW.request_id) != 'in_progress';
            SELECT RAISE(ABORT, 'episode must be good or usable')
            WHERE (SELECT quality FROM desk_episode WHERE id = NEW.episode_id) NOT IN ('good', 'usable');
            SELECT RAISE(ABORT, 'task mismatch') WHERE
                (SELECT task_name FROM desk_episode WHERE id = NEW.episode_id) !=
                (SELECT task_name FROM desk_datasetrequest WHERE id = NEW.request_id);
        END
    """,
    "assignment_update_guard": """
        BEFORE UPDATE ON desk_assignment BEGIN
            SELECT RAISE(ABORT, 'remove then assign instead of updating assignment');
        END
    """,
    "assignment_delete_guard": """
        BEFORE DELETE ON desk_assignment BEGIN
            SELECT RAISE(ABORT, 'request must be in progress')
            WHERE (SELECT status FROM desk_datasetrequest WHERE id = OLD.request_id) != 'in_progress';
        END
    """,
    "episode_update_guard": """
        BEFORE UPDATE ON desk_episode BEGIN
            SELECT RAISE(ABORT, 'imported episode metadata is immutable');
        END
    """,
    "request_owner_guard": """
        BEFORE INSERT ON desk_datasetrequest BEGIN
            SELECT RAISE(ABORT, 'request owner must be a client')
            WHERE (SELECT role FROM desk_user WHERE id = NEW.client_id) != 'client';
            SELECT RAISE(ABORT, 'new request must be submitted') WHERE NEW.status != 'submitted';
        END
    """,
    "request_fields_guard": """
        BEFORE UPDATE OF client_id, task_name, episodes_requested, deadline, notes ON desk_datasetrequest
        WHEN NEW.client_id != OLD.client_id OR NEW.task_name != OLD.task_name
            OR NEW.episodes_requested != OLD.episodes_requested OR NEW.deadline != OLD.deadline
            OR NEW.notes != OLD.notes
        BEGIN SELECT RAISE(ABORT, 'request specification is immutable'); END
    """,
    "client_role_guard": """
        BEFORE UPDATE OF role ON desk_user WHEN NEW.role != 'client'
        AND EXISTS (SELECT 1 FROM desk_datasetrequest WHERE client_id = OLD.id)
        BEGIN SELECT RAISE(ABORT, 'request owner must remain a client'); END
    """,
    "request_transition_guard": """
        BEFORE UPDATE OF status ON desk_datasetrequest WHEN OLD.status != NEW.status BEGIN
            SELECT RAISE(ABORT, 'invalid transition') WHERE NOT (
                (OLD.status = 'submitted' AND NEW.status = 'in_progress') OR
                (OLD.status = 'in_progress' AND NEW.status = 'delivered') OR
                (OLD.status = 'delivered' AND NEW.status IN ('accepted', 'rejected')) OR
                (OLD.status = 'rejected' AND NEW.status = 'in_progress')
            );
            SELECT RAISE(ABORT, 'insufficient episodes') WHERE NEW.status = 'delivered'
                AND (SELECT COUNT(*) FROM desk_assignment WHERE request_id = OLD.id) < NEW.episodes_requested;
        END
    """,
}


class Migration(migrations.Migration):
    dependencies = [("desk", "0001_initial")]
    operations = [migrations.RunSQL(
        sql=f"CREATE TRIGGER {name} {definition}",
        reverse_sql=f"DROP TRIGGER {name}",
    ) for name, definition in TRIGGERS.items()]
