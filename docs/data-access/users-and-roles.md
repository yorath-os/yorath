---
title: Users and roles
description: "Create users, give them a role and a department, and know what the owner can do that others can't."
---

The first user is the **owner**, created in the setup wizard. The owner sees all data, manages users, agents, models, keys and policies, and sees every user's API keys.

Every other user gets:

- a **role** — a word such as `member`, `manager` or `hr_admin`; documents are opened by role;
- a **department** — documents and records can be opened by department;
- optionally an **employee_id** — links the user to a row in a table, so "my own record" works.

## Add a user

1. Users → New user.
2. Fill in Name, Login, Password (at least 8 characters), Role and Department.
3. Click "Create user".

It worked if the user can sign in and, in Chat, gets answers only from the documents their role and department open.

## What users see

A user sees only the data their rights open, and an agent acting for them sees no more than that — see [Who sees what](who-sees-what.md). The HR demo set creates three users with different rights to try it.

## The owner's password

There is no password reset in the panel yet. If you can't sign in, see [Can't sign in](../troubleshooting/cant-sign-in.md).
