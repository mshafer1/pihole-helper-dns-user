# Pi-hole Helper - DNS User

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

<div style="border: 1px dashed;">
⚠️ Docs are currently written against Pi-hole Version 6. A different version of Pi-hole will likely not work with the API as is, and be different to setup ⚠️
</div>

&nbsp;

Since this is intended to be run as a docker container running next to pihole, a docker compose stack seems logical.


<details open><summary>Option 1: launcher script with pihole and side car together in same stack</summary>

&nbsp;

Steps:

1. Split `.env.example` into two files and customize

    ```bash
    touch ./hosting/_env_pihole ./hosting/_env_sidecar
    ```

    Copy the `pihole settings` section into `./hosting/_env_pihole`.

    Copy the `sidecar settings` section into `./hosting/_env_sidecar`.

    The comments in `.env.example` indicate which vars belong in each file.

1. Use the provided boot script and docker-compose file

    ```bash
    cd hosting
    bash launch_pihole.sh
    ```

    The Pihole admin interface will be available at `http://pihole.net:4080/admin` (if using the pihole as dns, otherwise, `http://<IP address>:4080/admin`). And the limited control panel will be available at `http://pihole.net` (or, `http://<IP address>`).

</details>

</details>
<summary>Option 2: With pihole on another host</summary>

&nbsp;

Steps:

1. Split `.env.example` into two files and customize

    ```bash
    touch ./hosting/_env_pihole ./hosting/_env_sidecar
    ```

    Copy the `pihole settings` section into `./hosting/_env_pihole`.

    Copy the `sidecar settings` section into `./hosting/_env_sidecar`.

    NOTE: in this mode, PIHOLE_HOST needs to be set such that the api is available at `http://${PIHOLE_HOST}/api`

1. Get an api token from the web UI

    - Go to `Settings` -> `Web Interface / API`
    - Click "Configure app password"
    - Copy the new app password, and click the button to apply it.

1. Store the app password in `./hosting/_env_sidecar` as the `PIHOLE_API_TOKEN` value

1. Bring up just the side car

    `cd image`
    `docker compose up -d pihole-helper-dns-user`

<details>
