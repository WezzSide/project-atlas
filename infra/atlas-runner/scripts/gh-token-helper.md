# GitHub token provisioning for the Atlas runner fabric

Contract: the controller needs a credential able to mint runner registration
material for exactly one repository. TOKEN_POSSESSION != AUTHORITY: the token
grants runner registration only.

## Option A — fine-grained personal access token (simplest)

1. GitHub → Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token.
2. Resource owner: the organization/user owning the target repository.
3. Repository access: only the target repository.
4. Permissions (minimal):
   - Administration: **read and write** (required for runner registration;
     scope it to the single repository).
   - Actions: **read** (queued run/job inspection).
   - Metadata: read (mandatory default).
5. Expiration: as short as operational practice allows.

## Option B — GitHub App (preferred for fleets)

Create a GitHub App with `actions:read` and `administration:write` on the
target repo, generate a private key, and let the controller mint an
installation token (JWT -> installation token). The v1 controller ships the
REST path with a static token; App JWT support is the documented upgrade path.

## Where the token goes on the host

The token is read from the environment variable `ATLAS_GITHUB_TOKEN`, which
is provided to the controller via the systemd unit's EnvironmentFile:

```
/etc/atlas-runner/config/atlas-runner.env
```

Permissions and ownership (enforced):

```
install -m 0600 -o atlas-runner -g atlas-runner /dev/null /etc/atlas-runner/config/atlas-runner.env
printf 'ATLAS_GITHUB_TOKEN=%s\n' '<token>' >> /etc/atlas-runner/config/atlas-runner.env
chmod 0600 /etc/atlas-runner/config/atlas-runner.env
```

The TOML config at `/etc/atlas-runner/config/atlas-runner.toml` must likewise
be mode 0600 owned by `atlas-runner`. Workers never read the token directly;
registration material is minted by the controller, written mode 0600 into the
per-job workspace, mounted into the worker container, and deleted immediately
after registration. Tokens are redacted from all logs and evidence.

Rotation: update the EnvironmentFile and `systemctl restart
atlas-runner-controller.service`. The token is only ever present in process
environment memory of the controller (never in worker containers).
