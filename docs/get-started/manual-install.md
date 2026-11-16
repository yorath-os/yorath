---
title: Install manually
description: "Clone a release with git and run the installer yourself, instead of the one-line command."
sidebar:
  order: 5
---

The one-line command downloads the same installer and does these steps for you. Do them by hand if you want to read every file first or your server can't reach `get.yorath.io`.

You need: a clean Ubuntu 24.04 x86_64 server, root, and `git` (`apt-get install -y git`).

## 1. Pick a release

The releases are on [GitHub](https://github.com/yorath-os/yorath/releases); take the latest tag, for example `v0.6.0`.

## 2. Clone that release

```sh
git clone --branch v0.6.0 https://github.com/yorath-os/yorath.git /opt/yorath
```

Clone into `/opt/yorath`: the `yorath` command and updates expect it there.

## 3. Read and run the installer

```sh
less /opt/yorath/installer/install.sh      # optional: read it first
sh /opt/yorath/installer/install.sh
```

Run from a clone, the installer installs the clone's version and never switches it. Options are variables before the command — see [Install variables](../reference/install-variables.md).

It worked if the installer ends with the setup wizard link: continue with [First hour](first-hour.md).

If it didn't: see [doctor and diag](../troubleshooting/doctor-and-diag.md).
