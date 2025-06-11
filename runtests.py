# pylint: skip-file
# Standard Library
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "testauth.settings.local")

    try:
        # Django
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    # Insérer 'test' au bon endroit dans sys.argv
    if len(sys.argv) == 1:
        # Si aucun argument, tester toute l'app
        sys.argv = ["runtests.py", "test", "fortunaisk"]
    else:
        # Si des arguments, insérer 'test' après le nom du script
        sys.argv.insert(1, "test")

    execute_from_command_line(sys.argv)
