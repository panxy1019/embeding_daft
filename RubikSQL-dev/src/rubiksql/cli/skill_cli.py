"""\
Skill management CLI commands for RubikSQL.
"""

from os import name
import click
from rubiksql.db import RUBIK_DBM


def register_skill_commands(cli):
    """\
    Register all skill management commands to the CLI.
    """


    @cli.group(
        "skill",
        help="""\
Manage skill component.

Subcommands:
  add           Add a new skill to the knowledge base
  clear         Clear all skills from the knowledge base
  list          List all skills in the knowledge base
  upsert        Upsert a skill in the knowledge base
  remove        Remove a skill from the knowledge base
  enable        Enable a skill in the knowledge base
  disable       Disable a skill in the knowledge base

Examples:
  rubiksql skill add -n mydb -p /path/to/skill      # Add a new skill to the knowledge base
  rubiksql skill clear -n mydb                      # Clear all skills from the knowledge base
  rubiksql skill list -n mydb                       # List all skills in the knowledge base
  rubiksql skill upsert -n mydb -p /path/to/skill   # Upsert a skill in the knowledge base
  rubiksql skill remove -n mydb -s skill_name       # Remove a skill from the knowledge base
  rubiksql skill enable -n mydb -s skill_name       # Enable a skill in the knowledge base
  rubiksql skill disable -n mydb -s skill_name      # Disable a skill in the knowledge base
""",
    )
    def build_cmd():
        """\
        Build knowledge base components.
        """
        pass


    def _load_klbase_context(db_name):
        from ahvn.utils.basic.path_utils import pj
        from ahvn.utils.basic.file_utils import exists_file
        from ahvn.utils.basic.serialize_utils import load_json
        from rubiksql.db import RUBIK_DBM
        from rubiksql.klbase import RubikSQLKLBase

        # Connect to database using RUBIK_DBM
        db = RUBIK_DBM.connect(db_name)
        try:
            # Get KB path from database folder
            db_dir = RUBIK_DBM._get_db_dir(db_name)
            kb_path = pj(db_dir, "kb")
            build_file = pj(kb_path, "build.json")

            # Check if KB is built
            if not exists_file(build_file):
                raise ValueError(
                    f"Knowledge base not built for database '{db_name}'. "
                    f"Run 'rubiksql kb build -db {db_name}' first."
                )

            build_state = load_json(build_file)
            if build_state.get("status") != "completed":
                raise ValueError(
                    f"Knowledge base build incomplete for database '{db_name}'. "
                    f"Run 'rubiksql kb build -db {db_name}' to complete the build."
                )

            # Load knowledge base (uses db_id only, database must be registered)
            klbase = RubikSQLKLBase(db_id=db_name)
            return klbase, db
        except Exception:
            db.close_conn()
            raise

    @build_cmd.command("add", help="Add a new skill to the knowledge base.")
    @click.option("--name", "-n", required=True, help="Database name.")
    @click.option("--path", "-p", required=True, help="Path to the skill directory.")
    def add_skill(name, path):
        """\
        Add a skill to the knowledge base.
        """
        try:
            from ahvn.utils.basic.color_utils import color_success, color_error
            from ahvn.ukf.templates.basic import SkillUKFT

            klbase, db = _load_klbase_context(name)
            try:
                # Load Skill
                click.echo(f"Loading skill from: {path}")
                skill = SkillUKFT.from_path(path)
                
                # Insert Skill
                klbase.upsert(kl=skill)
                
                click.echo(color_success(f"Successfully added skill to '{name}' knowledge base."))
            finally:
                db.close_conn()
            
        except Exception as e:
            from ahvn.utils.basic.color_utils import color_error
            click.echo(color_error(f"Error adding skill: {e}"), err=True)

    @build_cmd.command("clear", help="Clear all skills from the knowledge base.")
    @click.option("--name", "-n", required=True, help="Database name.")
    def clear_skills(name):
        """\
        Clear all skills from the knowledge base.
        """
        try:
            from ahvn.utils.basic.color_utils import color_success, color_error

            klbase, db = _load_klbase_context(name)
            try:
                # Clear Engine
                if "skills" in klbase.engines:
                    klbase.engines["skills"].clear()
                    click.echo(color_success("Cleared 'skills' engine."))
            finally:
                db.close_conn()
                
        except Exception as e:
            from ahvn.utils.basic.color_utils import color_error
            click.echo(color_error(f"Error clearing skills: {e}"), err=True)

    @build_cmd.command("list", help="List all skills in the knowledge base.")
    @click.option("--name", "-n", required=True, help="Database name.")
    def scan_skills(name):
        """\
        List all skills in the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_error, color_warning
        from rubiksql.api.knowledge import list_skills
        try:
            click.echo(f"Scanning skills for database '{name}'...")
            skills = list_skills(db_id=name)
            if not skills:
                click.echo(color_warning("No skills found in the knowledge base."))
            else:
                for skill in skills:
                    click.echo(skill)
        except ValueError as e:
            click.echo(color_error(f"Error scanning skills: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)


    @build_cmd.command("upsert", help="Upsert a skill in the knowledge base.")
    @click.option("--name", "-n", required=True, help="Database name.")
    @click.option("--path", "-p", required=True, help="Path to the skill directory.")
    @click.option("--skill", "-s", help="Optional skill name to upsert.")
    @click.option("--description", "-d", help="Optional skill description to upsert.")
    @click.option("--update", "-u", is_flag=True, help="Flag to update the skill if it exists.")
    @click.option("--role", "-r", type=click.Choice(["user", "system"]), default="user", help="Role of the skill (user or system).")
    def upsert_skill(name, path, skill, description, update, role):
        """\
        Upsert a skill in the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_error, color_grey, color_success
        from rubiksql.api.knowledge import upsert_skill

        try:
            # Load Skill
            click.echo(f"Upsert skill from: {path}")
            count = upsert_skill(
                db_id=name,
                path=path,
                name=skill,
                description=description,
                update=update,
                role=role
            )

            if count == 0:
                click.echo(color_grey("No skills were upserted."))
            else:
                click.echo(color_success(f"✓ Successfully upserted {count} skill{'s' if count != 1 else ''} to '{name}' knowledge base."))

        except (ValueError, FileNotFoundError) as e:
            click.echo(color_error(f"Error upserting skill: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)


    @build_cmd.command("remove", help="Remove a skill from the knowledge base.")
    @click.option("--name", "-n", required=True, help="Database name.")
    @click.option("--skill", "-s", required=True, help="Name of the skill to remove.")
    def remove_skill(name, skill):
        """\
        Remove a skill from the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_error, color_success
        from rubiksql.api.knowledge import remove_skill

        try:
            count = remove_skill(db_id=name, name=skill)
            if count == 0:
                click.echo(color_error(f"No skill named '{skill}' removed in '{name}' knowledge base."))
            else:
                click.echo(color_success(f"✓ Successfully removed skill '{skill}' from '{name}' knowledge base."))

        except ValueError as e:
            click.echo(color_error(f"Error removing skill: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)
        
    
    @build_cmd.command("enable", help="Enable a skill in the knowledge base.")
    @click.option("--name", "-n", required=True, help="Database name.")
    @click.option("--skill", "-s", required=True, help="Name of the skill to enable.")
    def enable_skill(name, skill):
        """\
        Enable a skill in the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_error, color_success
        from rubiksql.api.knowledge import enable_skill

        try:
            count = enable_skill(db_id=name, name=skill)
            if count == 0:
                click.echo(color_error(f"No skill named '{skill}' enabled in '{name}' knowledge base."))
            else:
                click.echo(color_success(f"✓ Successfully enabled skill '{skill}' in '{name}' knowledge base."))

        except ValueError as e:
            click.echo(color_error(f"Error enabling skill: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)


    @build_cmd.command("disable", help="Disable a skill in the knowledge base.")
    @click.option("--name", "-n", required=True, help="Database name.")
    @click.option("--skill", "-s", required=True, help="Name of the skill to disable.")
    def disable_skill(name, skill):
        """\
        Disable a skill in the knowledge base.
        """
        from ahvn.utils.basic.color_utils import color_error, color_success
        from rubiksql.api.knowledge import disable_skill

        try:
            count = disable_skill(db_id=name, name=skill)
            if count == 0:
                click.echo(color_error(f"No skill named '{skill}' disabled in '{name}' knowledge base."))
            else:
                click.echo(color_success(f"✓ Successfully disabled skill '{skill}' in '{name}' knowledge base."))

        except ValueError as e:
            click.echo(color_error(f"Error disabling skill: {e}"), err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(color_error(f"Unexpected error: {e}"), err=True)
            raise SystemExit(1)    