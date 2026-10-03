from django.db import migrations


INSERT_GUARD = """
    BEFORE INSERT ON desk_assignment BEGIN
        SELECT RAISE(ABORT, 'request must be in progress')
        WHERE (SELECT status FROM desk_datasetrequest WHERE id = NEW.request_id) != 'in_progress';
        SELECT RAISE(ABORT, 'episode must be good or usable')
        WHERE (SELECT quality FROM desk_episode WHERE id = NEW.episode_id) NOT IN ('good', 'usable');
        SELECT RAISE(ABORT, 'task mismatch') WHERE
            (SELECT task_name FROM desk_episode WHERE id = NEW.episode_id) !=
            (SELECT task_name FROM desk_datasetrequest WHERE id = NEW.request_id);
        SELECT RAISE(ABORT, 'request already has enough episodes') WHERE
            (SELECT COUNT(*) FROM desk_assignment WHERE request_id = NEW.request_id) >=
            (SELECT episodes_requested FROM desk_datasetrequest WHERE id = NEW.request_id);
    END
"""


OLD_INSERT_GUARD = """
    BEFORE INSERT ON desk_assignment BEGIN
        SELECT RAISE(ABORT, 'request must be in progress')
        WHERE (SELECT status FROM desk_datasetrequest WHERE id = NEW.request_id) != 'in_progress';
        SELECT RAISE(ABORT, 'episode must be good or usable')
        WHERE (SELECT quality FROM desk_episode WHERE id = NEW.episode_id) NOT IN ('good', 'usable');
        SELECT RAISE(ABORT, 'task mismatch') WHERE
            (SELECT task_name FROM desk_episode WHERE id = NEW.episode_id) !=
            (SELECT task_name FROM desk_datasetrequest WHERE id = NEW.request_id);
    END
"""


class Migration(migrations.Migration):
    dependencies = [("desk", "0002_domain_guards")]
    operations = [
        migrations.RunSQL(
            sql="DROP TRIGGER assignment_insert_guard",
            reverse_sql=f"CREATE TRIGGER assignment_insert_guard {OLD_INSERT_GUARD}",
        ),
        migrations.RunSQL(
            sql=f"CREATE TRIGGER assignment_insert_guard {INSERT_GUARD}",
            reverse_sql="DROP TRIGGER assignment_insert_guard",
        ),
    ]
