# Phase 2: hybrid APIs and serverless

Everything here talks to real AWS through boto3; nothing needs the web console. Run it with the
`.env.aws` profile (empty `AWS_ENDPOINT_URL`, real credentials). The scripts refuse to run if
`AWS_ENDPOINT_URL` is set, so they cannot hit Floci by mistake. Tables must already exist in AWS
(`make tables ENV_FILE=.env.aws`).

| Step | Command |
|---|---|
| One-time plumbing (SG, IAM role, instance profile) | `make aws-bootstrap ENV_FILE=.env.aws` |
| Spawn a node, wait for `/health`, print its public IP | `make aws-up ENV_FILE=.env.aws TAG=sha-xxxxxxx` |
| List / terminate burst nodes | `make aws-list` / `make aws-down` (same `ENV_FILE`) |
| Deploy the audit Lambda (stream + trigger) | `make lambda-deploy ENV_FILE=.env.aws` |
| Fire real item events, then read the Lambda logs | `make lambda-test` / `make lambda-logs` |

`node.py up|down` print JSON on stdout (instance id, public IP, seconds to running/healthy) and
progress on stderr, so the Phase 3 controller can call them or import `node.up()` / `node.down()`.
`policies/operator-policy.json` is the least-privilege policy for the identity that runs them:
only `t2.micro`/`t3.micro`, only instances tagged `Project=paas`.
