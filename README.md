# Pi-hole Limited User Side Car

## Why?

I want to setup a Pi-hole server for some friends.
They should be able to admin what is blocked or not themselves,
but I don't want to give them access to other settings like the upstream DNS.

Pi-hole itself does not have a users capability. You are either logged in as admin, or you are read only.

## What is a sidecar?

> n. A helper container or process that runs alongside a main application.

Running this project as a companion Docker container to the main pi-hole adds another password
that can be used for viewing recent queries, managing blocked domains,
and choosing whether to use block lists or not.

(but all other settings are not exposed)

## How to deploy

Since this is intended to be run as a docker container running next to pihole, a docker compose stack seems logical.

Steps:

1. Generate the api token:

```bash
echo -n "your_secure_api_token_here" | sha256sum
```

1. Copy `.env.example` and customize

```bash
cp .env.example .env
```

Edit the file (see comments for what to set each to.)

1. Use the provided boot script and docker-compose file

```bash
cd hosting
bash launch_pihole.sh
```

The Pihole admin interface will be available at `http://pihole.net:4080/admin` (if using the pihole as dns, otherwise, `http://<IP address>:4080/admin`). And the limited control panel will be available at `http://pihole.net` (or, `http://<IP address>`).
