---
title: Installation
description: "What the install command does, how long it takes, which options it has and how to run it again."
sidebar:
  order: 3
---

```sh
curl -fsSL https://get.yorath.io | sh
```

The script at `get.yorath.io` is the installer of the latest release: [read it](https://github.com/yorath-os/yorath/blob/main/installer/install.sh) before running. It installs exactly that release from the public repository into `/opt/yorath`; to pick another one, set `YORATH_VERSION`:

```sh
curl -fsSL https://get.yorath.io | YORATH_VERSION=v0.6.0 sh
```

## What it does

1. Checks the system: Ubuntu 24.04, x86_64, root.
2. Waits for Ubuntu's automatic updates if they hold `apt` (up to 20 minutes on a fresh server).
3. Installs Docker and git, and the GPU driver for containers if there is a GPU.
4. Clones the release into `/opt/yorath`, generates passwords and keys into `installer/.env.local`.
5. Issues a certificate for the server's IP from its own CA (or for your domain — see [Install variables](../reference/install-variables.md#https)).
6. Builds the images and starts the services: database, Ollama, core, web panel, the egress proxy.
7. Installs the `yorath` command and prints the link to the setup wizard.

Time: ~10 minutes for the install and ~20 minutes in the wizard, mostly pulling the model.

The install asks nothing in the terminal: every option is a variable set before the command — see [Install variables](../reference/install-variables.md).

## Run it again

Running the installer again is safe: secrets are kept and services are rebuilt. It picks up a new GPU or more memory. It doesn't change the version — **to update, run `yorath update <version>`**: see [Update and rollback](../operate/update-and-rollback.md).

It worked if the installer ends with the wizard link and `yorath status` lists the services as running.

If it didn't: see [doctor and diag](../troubleshooting/doctor-and-diag.md).
