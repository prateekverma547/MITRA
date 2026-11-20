# Deploying to AWS (Mumbai, Terraform)

The backend and the bot processes ship as **one image**. FastAPI serves the
employer API and the candidate join page, and spawns `python -m bot.run_bot` as a
child process for each interview. Same dependencies, same container.

**Target:** AWS region **`ap-south-1` (Mumbai)**, because candidates are in India.
Infrastructure is defined in Terraform under `infra/`.

> **Status.** This document describes the target. Read the status line on each
> step before running it:
>
> - **Not yet written:** the `infra/` Terraform code.
> - **Done:** the startup guard. The app now needs `APP_ENV=production` to count as
>   deployed, refuses to start without `DATABASE_URL`, and refuses SQLite in production.
>   The task definition must set `APP_ENV=production` (step 3b). Without it the guard
>   is off, so do not deploy until that variable is set.

---

## Architecture

| Concern | AWS service | Why |
|---|---|---|
| Compute | **ECS on Fargate**, one task, desired count 1, no autoscaling | A bot is a child process of one container. A second task splits the capacity counter and can orphan a live interview. |
| Database | **RDS for PostgreSQL 17**, private subnets, automated backups | The record of truth. Matches the Postgres 17 used in local compose and the restore drill. |
| Traffic | **Application Load Balancer**, HTTPS via **ACM**, health check `/health` | TLS terminates here and the load balancer sets `X-Forwarded-For` and `X-Forwarded-Proto`, which the app relies on. |
| Secrets | **Secrets Manager**, injected into the task at start | No keys in the image, the task definition, or the repo. |
| Recordings | **S3** bucket in `ap-south-1`, private, with a lifecycle rule | The app already stores recordings there when `RECORDINGS_S3_BUCKET` is set. |
| Images | **ECR** in `ap-south-1` | Where the built image is pushed. |
| Logs | **CloudWatch Logs** | Replaces the Railway log view. |
| DNS | **Route 53** record for the chosen domain | Candidates and admins use one domain. |

Terraform state lives in an S3 bucket with versioning, created once by
`infra/bootstrap/`.

---

## Before you start

You need:

- An AWS account with an IAM user or role that can create the resources above.
- Terraform 1.9 or later, and the AWS CLI, with a profile configured for `ap-south-1`.
- A domain you control, and access to its DNS (`DOMAIN` below).
- The API keys: OpenAI, ElevenLabs, Daily, and Deepgram if you use it.

Nothing in this document creates billable resources until you run
`terraform apply`. Run `terraform plan` first and read it.

---

## 1. Bootstrap the Terraform state (once per account)

*Status: written, validated. Not yet applied.*

```bash
cd infra/bootstrap
terraform init
terraform apply -var "state_bucket_name=mitra-tfstate-<your-account-id>"
```

This creates the bucket that holds Terraform state. Its own state stays local, and the
bucket is protected from destruction. Copy the bucket name into
`infra/envs/staging/backend.hcl`, from `backend.hcl.example`.

## 2. Configure the staging environment

*Status: written, validated. Not yet applied.*

Staging comes first, production after it has been proven. The production environment is
the same code with its own `terraform.tfvars`, and is created once staging has run a full
interview.

```bash
cd infra/envs/staging
cp terraform.tfvars.example terraform.tfvars   # fill in every CHANGE-ME
cp backend.hcl.example backend.hcl             # fill in the bucket from step 1
terraform init -backend-config=backend.hcl
```

The variables that matter:

| variable | default | notes |
|---|---|---|
| `aws_region` | `ap-south-1` | pinned by validation to Mumbai |
| `domain_name` | none, required | the hostname candidates use |
| `hosted_zone_name` | none, required | the Route 53 zone that contains it; must already exist |
| `image_tag` | none, required | the commit pushed to ECR (step 5) |
| `desired_count` | `0` | keep at 0 until the image and secrets exist, then 1 |
| `task_cpu`, `task_memory` | `4096`, `8192` | 4 vCPU and 8 GB. Fargate has no 8 vCPU / 8 GB option |
| `max_concurrent_interviews` | `3` | lower than the docs' 6, which assumed 8 vCPU; raise from measurements |
| `db_multi_az` | `false` | pilot setting; turn on before real candidates |
| `db_deletion_protection` | `true` | `terraform destroy` refuses the database |
| `alarm_email` | none, required | must click the AWS confirmation email before alerts arrive |

## 3. Create the infrastructure

*Status: written, validated. Not yet applied. Run `terraform plan` and read it first.*

```bash
cd infra/envs/staging
terraform plan -out=tfplan     # read this before applying
terraform apply tfplan
```

The first apply creates the network, the database, the load balancer, the certificate,
the bucket, the registry, the cluster and the secrets. The service starts with zero tasks,
because there is no image yet. Step 4 sets the secrets, and step 5 pushes the image.

Terraform writes the database password and the admin password into state. The state
bucket is encrypted and private, so protect access to it.

## 3b. Logging settings in the task definition

*Status: written in `infra/envs/staging/ecs.tf`, validated.*

The task sends its output to CloudWatch Logs through the `awslogs` driver, with these
plain variables:

```
APP_ENV    = production
LOG_FORMAT = json
LOG_LEVEL  = INFO
```

`APP_ENV=production` turns on the startup guard: the app refuses to start without a
Postgres `DATABASE_URL`, and refuses SQLite. It also requires the recording bucket when
recording is on. It is set here, in Terraform, and never left to a default.

`LOG_FORMAT=json` makes each line one JSON object, which CloudWatch Logs Insights can
filter by field (`interview_id`, `request_id`, `status`). The log group's retention is
set in Terraform, not left at "never expire". Candidate and interview IDs are personal
data, and the retention period is the limit on how long they are kept. The queries are
in `docs/OPERATIONS.md`.

## 4. Store the secrets

*Status: secret containers written in `secrets.tf`. Values are set from the command line below.*

Set the values in Secrets Manager, not in the repo or the task definition:

```bash
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id mitra/staging/OPENAI_API_KEY --secret-string "sk-..."
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id mitra/staging/ELEVENLABS_API_KEY --secret-string "..."
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id mitra/staging/DAILY_API_KEY --secret-string "..."
aws secretsmanager put-secret-value --region ap-south-1 \
  --secret-id mitra/staging/ADMIN_PASSWORD --secret-string "$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
```

`ELEVENLABS_VOICE_ID`, the model names and `DEFAULT_TIMEZONE` are not secret and
go in the task definition as plain variables. The model defaults are in CLAUDE.md.

`DATABASE_URL` is **not** set by hand. Terraform builds it from the RDS endpoint
and the generated database password, and stores it as a secret.

## 5. Build and publish the image

*Status: the Dockerfile exists; the publish script is not yet written.*

```bash
REGISTRY=$(terraform -chdir=infra/envs/staging output -raw ecr_repository_url)
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin "${REGISTRY%%/*}"
docker build -f backend/Dockerfile -t "$REGISTRY:$(git rev-parse --short HEAD)" .
docker push "$REGISTRY:$(git rev-parse --short HEAD)"
```

The build context is the repository root, not `backend/`. Tag each image with the
commit, so a rollback is pointing the service at an earlier tag.

## 6. Point the service at the image and deploy

*Status: written. Run against staging first.*

```bash
terraform -chdir=infra/envs/staging apply -var "image_tag=$(git rev-parse --short HEAD)" -var "desired_count=1"
```

The container's start command runs `alembic upgrade head` before it serves, so the
schema is brought to head on every start. The app refuses to start on a database
that is behind, so a partial migration cannot serve traffic.

Then check it:

```bash
curl https://<DOMAIN>/health
```

---

## Before every deploy

**Check that no interview is running.** A deploy replaces the task, and ECS sends
the bot processes a stop signal. A live interview is lost.

Query the database directly. The database sits in a private subnet and is not
reachable from the internet. Reach it through ECS Exec, which is enabled on the service
(`ecs.tf`). Open a shell in the running task and run `psql` there:

```bash
psql "$DATABASE_URL" -tAc "select count(*) from interviews where status = 'in_progress';"
```

Deploy only when that is `0`.

## Rollback

Point the service at the previous image tag and apply:

```bash
terraform -chdir=infra/envs/staging apply -var "image_tag=<previous-commit>"
```

If the new version ran a migration, a rollback to an older code version needs the
database downgraded first. Migrations are reversible, but check the downgrade on a
copy before relying on it: `alembic downgrade -1` from the revision the older code
expects.

---

## Running an interview on the deployed app

There is no employer UI yet, so bookings go through the API, with an admin session
(`POST /admin/login` first):

```bash
# Book an interview for a candidate whose blueprint is ready
curl -X POST https://<DOMAIN>/candidates/<cand_id>/interviews
```

That returns a meeting ID and password. The candidate then goes to:

```
https://<DOMAIN>/join
```

## Backups and restores

RDS takes automated daily snapshots, with the retention period set by
`db_backup_retention_days`. These are a second layer, not a replacement for the
script, which produces a dump you can restore anywhere:

```bash
DATABASE_URL=... python backend/scripts/db_backup.py --dir /secure/location --keep 14
```

*Status: the scheduled job is not yet written.* Until it is, backups are manual.
The restore drill in `docs/OPERATIONS.md` must be run against RDS, not only against
a local container, before this is relied on.

## What to watch on the first deployed interview

This deploy exists to answer questions we have been carrying since the first day of
the project and still have no evidence for:

1. **Does a bot subprocess survive a full 40-minute session?** The architecture
   assumes yes. Nothing has tested it.
2. **Does WebRTC hold up from another network?** Every session so far has been on
   one laptop.
3. **What does memory look like?** Each bot loads Silero VAD and the smart-turn
   ONNX model. Concurrent interviews mean concurrent copies.
4. **Is latency acceptable from Mumbai?** `ttfa_median_ms` in the session record
   answers this directly. Check it against the APIs' own regions as well: OpenAI,
   ElevenLabs, Deepgram and Daily each serve from their own endpoints.
5. **Can you follow one interview in the logs?** Run the first Insights query in
   `docs/OPERATIONS.md` for that interview's ID. The web and bot lines should both
   appear, with a `request_id` on each web request.

## Known constraints

- **No deploys during a live interview.** A deploy kills every in-flight bot. The
  check above is the mitigation.
- **Stop signal.** The Dockerfile sets `STOPSIGNAL SIGINT`, which applies to
  `docker stop`. ECS sends `SIGTERM` regardless. *To verify on the first deploy:*
  that a bot and the web process both exit cleanly on `SIGTERM` inside the grace
  period, so a transcript is saved.
- **Stop timeout.** The task's stop timeout is set to 120 seconds, the longest
  Fargate allows, so a bot has time to save its transcript.
- **Session JSON files are ephemeral.** The database is the record of truth. The
  files under `backend/sessions/` vanish with the task.
- **Image size is about 2.5GB.** The `local-smart-turn` extra is deliberately
  excluded, see CLAUDE.md.
