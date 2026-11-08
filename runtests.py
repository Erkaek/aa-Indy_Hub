"""Run the test project with isolated local infrastructure."""

# Standard Library
import os
import sys

if __name__ == "__main__":
    os.environ["DJANGO_SETTINGS_MODULE"] = "testauth.settings.local"

    # Local
    # AA Example App
    from testauth.runner import execute

    execute(sys.argv[1:])
