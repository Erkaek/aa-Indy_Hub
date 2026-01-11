# pylint: skip-file
# Standard Library
import os
import sys

if __name__ == "__main__":
    # Force the test settings module so external environments don't accidentally
    # run tests with a different settings module.
    os.environ["DJANGO_SETTINGS_MODULE"] = "testauth.settings.local"
    try:
        # Django
        from django.core.management import execute_from_command_line
    except ImportError:
        # The above import may fail for some other reason. Ensure that the
        # issue is really that Django is missing to avoid masking other
        # exceptions on Python 2.
        try:
            # Django
            import django  # noqa: F401
        except ImportError:
            raise ImportError(
                "Couldn't import Django. Are you sure it's installed and "
                "available on your PYTHONPATH environment variable? Did you "
                "forget to activate a virtual environment?"
            )
        raise
    execute_from_command_line([sys.argv[0], "test", *sys.argv[1:]])
