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

TODO
