# Resolution Process (legacy installs from previous merges)

This procedure helps restore a clean `indy_hub` installation when an older merge-based install left residual files behind.

## Procedure

1. Stop Alliance Auth:

   ```bash
   supervisorctl stop all
   ```

2. Uninstall the package:

   ```bash
   pip uninstall indy_hub
   ```

3. Check the venv `site-packages` directory and manually remove the `indy_hub` folder if files are still present.

4. Reinstall `indy_hub`.

5. Restart Supervisor:

   ```bash
   supervisorctl start all
   ```
