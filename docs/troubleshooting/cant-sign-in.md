---
title: Can't sign in
description: "The setup wizard didn't ask for an owner, or the owner's password is lost: why it happens and how to get back in."
---

## The wizard didn't ask me to create the owner

The "Create the owner" form appears only while there are no users. If Yorath was installed on this server before, running the installer again keeps the database — and the owner created then. Sign in with that owner's login.

## I lost the owner's password

There is no password reset in the panel yet (it's on the [Roadmap](https://yorath.io/roadmap/)). Create another owner from the server and sign in with it:

```sh
cd /opt/yorath
read -s -p 'New password: ' P; echo        # stays out of the shell history
printf '%s\n' "$P" | docker compose --project-directory installer -f installer/compose.yaml \
  exec -T core yorath create-user --login admin2 --role owner
unset P
```

The command reads the password from its standard input (one line).

It worked if you can sign in as `admin2` and see the Users screen. There, check the old owner's account.

If it didn't: run `yorath doctor` — the core must be running — and see [doctor and diag](doctor-and-diag.md).
